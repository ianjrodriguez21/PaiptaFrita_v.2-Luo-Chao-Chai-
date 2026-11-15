import os
import json
import uuid
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

TELEMETRY_BUCKET_NAME = os.environ.get(
    "S3_BUCKET_NAME",
    "bucket-telemetria-historico"
)

ANOMALIES_TABLE_NAME = os.environ.get(
    "ANOMALIES_TABLE_NAME",
    "t_anomalias"
)

INCIDENTS_TABLE_NAME = os.environ.get(
    "INCIDENTS_TABLE_NAME",
    "t_incidentes"
)

ANALYSIS_TABLE_NAME = os.environ.get(
    "ANALYSIS_TABLE_NAME",
    "t_analisis"
)

ANALYSIS_BUCKET_NAME = os.environ.get(
    "ANALYSIS_BUCKET_NAME", ""
)

anomalies_table = dynamodb.Table(
    ANOMALIES_TABLE_NAME
)

incidents_table = dynamodb.Table(
    INCIDENTS_TABLE_NAME
)

analysis_table = dynamodb.Table(
    ANALYSIS_TABLE_NAME
)


def parse_timestamp(value):
    """Interpreta fechas ISO 8601 en UTC."""

    if not isinstance(value, str):
        raise ValueError("timestamp inválido")

    date = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if date.tzinfo is None:
        raise ValueError(
            "timestamp necesita zona horaria"
        )

    return date.astimezone(timezone.utc)


def get_historical_telemetry(
    tenant_id, machine_id, start_time, end_time
):
    """Obtiene las lecturas históricas desde S3."""

    records = []
    prefix = f"{tenant_id}/{machine_id}/"

    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(
        Bucket=TELEMETRY_BUCKET_NAME,
        Prefix=prefix
    ):
        for obj in page.get("Contents", []):

            key = obj["Key"]

            if not key.endswith(".json"):
                continue

            response = s3.get_object(
                Bucket=TELEMETRY_BUCKET_NAME,
                Key=key
            )

            telemetry = json.loads(
                response["Body"].read()
            )

            if not isinstance(telemetry, dict):
                continue

            if (
                telemetry.get("tenant_id") != tenant_id
                or telemetry.get("machine_id") != machine_id
            ):
                continue

            timestamp = telemetry.get("timestamp")

            if not timestamp:
                continue

            try:
                record_time = parse_timestamp(timestamp)
            except (ValueError, TypeError):
                logger.warning(
                    "Registro con timestamp inválido: %s",
                    key
                )
                continue

            if start_time <= record_time <= end_time:
                records.append(telemetry)

    records.sort(
        key=lambda item: parse_timestamp(
            item["timestamp"]
        )
    )

    return records


def query_machine_records(
    table, tenant_id, machine_id,
    start_time, end_time
):
    """
    Consulta registros de un tenant y filtra
    por máquina y timestamp.

    Versión inicial sin dependencia de GSI.
    """

    records = []
    last_key = None

    while True:
        params = {
            "KeyConditionExpression": (
                Key("tenant_id").eq(tenant_id)
            )
        }

        if last_key:
            params["ExclusiveStartKey"] = last_key

        response = table.query(**params)

        for record in response.get("Items", []):

            if record.get("machine_id") != machine_id:
                continue

            record_timestamp = (
                record.get("timestamp")
                or record.get("detected_at")
            )

            if not record_timestamp:
                continue

            try:
                record_time = parse_timestamp(
                    record_timestamp
                )
            except (ValueError, TypeError):
                continue

            if start_time <= record_time <= end_time:
                records.append(record)

        last_key = response.get("LastEvaluatedKey")

        if not last_key:
            break

    return records


def get_value(record, variable):
    try:
        value = record.get(variable)

        if value is None:
            return None

        return float(value)

    except (ValueError, TypeError):
        return None


def calculate_trend(values):
    """Compara la primera y segunda mitad histórica."""

    if len(values) < 2:
        return 0.0

    middle = len(values) // 2

    first_half = values[:middle]
    second_half = values[middle:]

    old_average = (
        sum(first_half) / len(first_half)
    )

    new_average = (
        sum(second_half) / len(second_half)
    )

    if old_average == 0:
        return 0.0

    change = (
        (new_average - old_average)
        / abs(old_average)
    ) * 100

    return round(change, 2)


def lambda_handler(event, context):
    try:
        data = event.get("Payload", event)

        tenant_id = data.get("tenant_id")
        machine_id = data.get("machine_id")

        if not tenant_id or not machine_id:
            raise ValueError(
                "tenant_id y machine_id son obligatorios"
            )

        window_days = int(
            data.get("analysis_window_days", 30)
        )

        if not 1 <= window_days <= 365:
            raise ValueError(
                "analysis_window_days fuera de rango"
            )

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(
            days=window_days
        )

        # 1. Histórico de telemetría desde S3
        telemetry_records = get_historical_telemetry(
            tenant_id,
            machine_id,
            start_time,
            end_time
        )

        # 2. Anomalías e incidentes desde DynamoDB
        anomaly_records = query_machine_records(
            anomalies_table,
            tenant_id,
            machine_id,
            start_time,
            end_time
        )

        incident_records = query_machine_records(
            incidents_table,
            tenant_id,
            machine_id,
            start_time,
            end_time
        )

        findings = []
        max_trend = 0.0

        # 3. Analizar tendencias
        variables = [
            "temperature",
            "vibration",
            "pressure",
            "fuel_consumption"
        ]

        for variable in variables:
            values = [
                get_value(record, variable)
                for record in telemetry_records
            ]

            values = [
                v for v in values
                if v is not None
            ]

            if len(values) < 2:
                continue

            trend = calculate_trend(values)

            if trend >= 15.0:
                findings.append(
                    f"{variable}_increasing"
                )

                max_trend = max(
                    max_trend,
                    trend
                )

        similar_incidents = len(
            incident_records
        )

        previous_anomalies = len(
            anomaly_records
        )

        if previous_anomalies >= 3:
            findings.append(
                "frequent_historical_anomalies"
            )

        if similar_incidents >= 2:
            findings.append(
                "recurrent_past_incidents"
            )

        degradation_detected = any(
            finding.endswith("_increasing")
            for finding in findings
        )

        recurrence_detected = (
            degradation_detected
            or previous_anomalies >= 3
            or similar_incidents >= 2
        )

        historical_pattern_detected = (
            len(findings) > 0
        )

        if degradation_detected:
            pattern_type = "degradacion_progresiva"

        elif (
            previous_anomalies >= 3
            or similar_incidents >= 2
        ):
            pattern_type = "picos_frecuentes"

        else:
            pattern_type = None

        # 4. Calcular riesgo histórico
        trend_points = (
            min(40.0, max_trend * 1.2)
            if degradation_detected else 0.0
        )

        anomaly_points = min(
            30.0,
            previous_anomalies * 6.0
        )

        incident_points = min(
            30.0,
            similar_incidents * 12.0
        )

        historical_risk_score = round(
            min(
                100.0,
                trend_points
                + anomaly_points
                + incident_points
            )
        )

        historical_result = {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "historical_pattern_detected": (
                historical_pattern_detected
            ),
            "pattern_type": pattern_type,
            "findings": findings,
            "similar_incidents": similar_incidents,
            "previous_anomalies": previous_anomalies,
            "recurrence_detected": recurrence_detected,
            "historical_risk_score": historical_risk_score
        }

        # 5. Guardar resultado en t_analisis
        timestamp = end_time.isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z")

        machine_timestamp = (
            f"{machine_id}#{timestamp}"
        )

        analysis_item = {
            **historical_result,
            "tenant_id": tenant_id,
            "sk": machine_timestamp,
            "machine_timestamp": machine_timestamp,
            "analysis_id": str(uuid.uuid4()),
            "machine_id": machine_id,
            "timestamp": timestamp
        }

        analysis_table.put_item(
            Item=json.loads(
                json.dumps(analysis_item),
                parse_float=Decimal
            )
        )

        # 6. Opcional: guardar análisis en S3
        if ANALYSIS_BUCKET_NAME:
            s3_key = (
                f"{tenant_id}/{machine_id}/"
                f"{timestamp}.json"
            )

            s3.put_object(
                Bucket=ANALYSIS_BUCKET_NAME,
                Key=s3_key,
                Body=json.dumps(
                    historical_result
                ).encode("utf-8"),
                ContentType="application/json"
            )

        # 7. Mantener contrato Step Functions
        anomaly_result = data.get(
            "anomaly_result"
        ) or {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "anomaly_detected": data.get(
                "anomaly_detected", False
            ),
            "anomalies": data.get("anomalies", [])
        }

        pattern_result = data.get(
            "pattern_result"
        ) or {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "pattern_detected": data.get(
                "pattern_detected", False
            ),
            "confidence": data.get(
                "confidence", 0.0
            )
        }

        return {
            **historical_result,
            "anomaly_result": anomaly_result,
            "pattern_result": pattern_result,
            "historical_result": historical_result,
            "machine_data": data.get(
                "machine_data", {}
            )
        }

    except Exception:
        logger.exception(
            "Error en análisis histórico"
        )
        raise
