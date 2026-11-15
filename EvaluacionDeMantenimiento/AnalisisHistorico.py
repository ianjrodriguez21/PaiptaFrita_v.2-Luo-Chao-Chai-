"""
Lambda: AnalisisHistorico

Entrada:
    Contexto completo de Step Functions.

Salida:
    historical_result (JSON directo).

Almacenamiento:
    S3: telemetria historica
    DynamoDB: t_anomalias, t_incidentes, t_analisis

PK de todas las tablas: tenant_id
SK de t_analisis: sk
"""

import os
import json
import logging
import hashlib
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

S3_BUCKET_NAME = os.environ["S3_BUCKET_NAME"]

ANOMALIES_TABLE_NAME = os.environ.get(
    "ANOMALIES_TABLE_NAME", "t_anomalies"
)

INCIDENTS_TABLE_NAME = os.environ.get(
    "INCIDENTS_TABLE_NAME", "t_incidents"
)

ANALYSIS_TABLE_NAME = os.environ.get(
    "ANALYSIS_TABLE_NAME", "t_analysis"
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
    if not isinstance(value, str):
        raise ValueError("timestamp obligatorio")

    date = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if date.tzinfo is None:
        raise ValueError(
            "timestamp debe tener zona horaria"
        )

    return date.astimezone(timezone.utc)


def to_timestamp(date):
    return date.isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def get_historical_telemetry(
    tenant_id, machine_id, start_time, end_time
):
    """
    Recupera lecturas anteriores al evento
    desde S3.
    """

    records = []

    prefix = f"{tenant_id}/{machine_id}/"

    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(
        Bucket=S3_BUCKET_NAME,
        Prefix=prefix
    ):
        for obj in page.get("Contents", []):

            key = obj["Key"]

            if not key.endswith(".json"):
                continue

            response = s3.get_object(
                Bucket=S3_BUCKET_NAME,
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

            try:
                record_time = parse_timestamp(
                    telemetry.get("timestamp")
                )
            except (ValueError, TypeError):
                logger.warning(
                    "Timestamp invalido: %s",
                    key
                )
                continue

            # Solo registros anteriores al evento actual
            if start_time <= record_time < end_time:
                records.append(telemetry)

    records.sort(
        key=lambda item: parse_timestamp(
            item["timestamp"]
        )
    )

    return records


def query_historical_records(
    table, tenant_id, machine_id,
    start_time, end_time
):
    """
    Consulta DynamoDB utilizando tenant_id.
    Filtra por maquina y fecha en Python.

    Version inicial sin depender de un GSI.
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

            timestamp = (
                record.get("timestamp")
                or record.get("detected_at")
            )

            if not timestamp:
                continue

            try:
                record_time = parse_timestamp(
                    timestamp
                )
            except (ValueError, TypeError):
                continue

            if start_time <= record_time < end_time:
                records.append(record)

        last_key = response.get("LastEvaluatedKey")

        if not last_key:
            break

    return records


def calculate_trend(values):
    """
    Compara el promedio de la primera mitad
    con el promedio de la segunda mitad.
    """

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

    percentage = (
        (new_average - old_average)
        / abs(old_average)
    ) * 100

    return round(percentage, 2)


def get_numeric_value(record, variable):
    try:
        value = record.get(variable)

        if value is None or isinstance(value, bool):
            return None

        number = float(value)

        if not math.isfinite(number):
            return None

        return number

    except (ValueError, TypeError, OverflowError):
        return None


def lambda_handler(event, context):
    try:
        # 1. Contexto Step Functions
        tenant_id = event.get("tenant_id")
        machine_id = event.get("machine_id")
        timestamp = event.get("timestamp")

        if not tenant_id or not machine_id:
            raise ValueError(
                "tenant_id y machine_id son obligatorios"
            )

        event_time = parse_timestamp(timestamp)

        window_days = int(
            event.get("analysis_window_days", 30)
        )

        if not 1 <= window_days <= 365:
            raise ValueError(
                "analysis_window_days fuera de rango"
            )

        start_time = event_time - timedelta(
            days=window_days
        )

        # 2. Consultar historial S3
        telemetry_records = get_historical_telemetry(
            tenant_id,
            machine_id,
            start_time,
            event_time
        )

        # 3. Consultar historial DynamoDB
        anomaly_records = query_historical_records(
            anomalies_table,
            tenant_id,
            machine_id,
            start_time,
            event_time
        )

        incident_records = query_historical_records(
            incidents_table,
            tenant_id,
            machine_id,
            start_time,
            event_time
        )

        # 4. Analizar tendencias
        findings = []
        max_trend = 0.0

        variables = [
            "temperature",
            "vibration",
            "pressure",
            "fuel_consumption"
        ]

        for variable in variables:
            values = [
                get_numeric_value(record, variable)
                for record in telemetry_records
            ]

            values = [
                value for value in values
                if value is not None
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

        previous_anomalies = len(
            anomaly_records
        )

        similar_incidents = len(
            incident_records
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

        historical_pattern_detected = bool(
            findings
        )

        if degradation_detected:
            pattern_type = "degradacion_progresiva"

        elif recurrence_detected:
            pattern_type = "picos_frecuentes"

        else:
            pattern_type = None

        # 5. Calcular riesgo historico
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

        result = {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "timestamp": to_timestamp(event_time),
            "historical_pattern_detected": (
                historical_pattern_detected
            ),
            "pattern_type": pattern_type,
            "findings": findings,
            "similar_incidents": similar_incidents,
            "previous_anomalies": previous_anomalies,
            "recurrence_detected": recurrence_detected,
            "historical_risk_score": historical_risk_score,
            "telemetry_records_analyzed": len(
                telemetry_records
            ),
            "analysis_window_days": window_days
        }

        # 6. Guardar resultado en t_analisis
        machine_timestamp = (
            f"{machine_id}#{to_timestamp(event_time)}"
        )

        analysis_id = (
            "HIS-"
            + hashlib.sha256(
                f"{tenant_id}|{machine_timestamp}".encode()
            ).hexdigest()[:16].upper()
        )

        item = {
            **result,
            "sk": machine_timestamp,
            "machine_timestamp": machine_timestamp,
            "analysis_id": analysis_id
        }

        dynamodb_item = json.loads(
            json.dumps(item),
            parse_float=Decimal
        )

        # El mismo evento reemplaza su propio analisis,
        # en lugar de generar un registro duplicado.
        analysis_table.put_item(
            Item=dynamodb_item
        )

        # 7. Opcional: guardar en S3
        if ANALYSIS_BUCKET_NAME:
            key = (
                f"{tenant_id}/{machine_id}/"
                f"{to_timestamp(event_time)}.json"
            )

            s3.put_object(
                Bucket=ANALYSIS_BUCKET_NAME,
                Key=key,
                Body=json.dumps(result).encode("utf-8"),
                ContentType="application/json"
            )

        logger.info(
            "Analisis historico completado: %s",
            machine_id
        )

        # 8. Salida directa para Step Functions
        return result

    except Exception:
        logger.exception(
            "Error en AnalisisHistorico"
        )
        raise
