import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")

# [CORREGIDO]: Tablas alineadas con el diagrama (t_maquina, t_anomalias, t_incidentes y salida t_analisis + S3)
TELEMETRY_TABLE_NAME = os.environ.get("TELEMETRY_TABLE_NAME", "t_maquina")
ANOMALIES_TABLE_NAME = os.environ.get("ANOMALIES_TABLE_NAME", "t_anomalias")
INCIDENTS_TABLE_NAME = os.environ.get("INCIDENTS_TABLE_NAME", "t_incidentes")
ANALYSIS_TABLE_NAME = os.environ.get("ANALYSIS_TABLE_NAME", "t_analisis")
ANALYSIS_BUCKET_NAME = os.environ.get("ANALYSIS_BUCKET_NAME", "")

telemetry_table = dynamodb.Table(TELEMETRY_TABLE_NAME)
anomalies_table = dynamodb.Table(ANOMALIES_TABLE_NAME)
incidents_table = dynamodb.Table(INCIDENTS_TABLE_NAME)
analysis_table = dynamodb.Table(ANALYSIS_TABLE_NAME)

INDEX_NAME = "tenant-machine-timestamp-index"


def query_machine_records(table, tenant_id, machine_id, start_time, end_time):
    records = []
    last_key = None
    start_key = f"{machine_id}#{start_time}"
    end_key = f"{machine_id}#{end_time}"

    while True:
        params = {
            "IndexName": INDEX_NAME,
            "KeyConditionExpression": (
                Key("tenant_id").eq(tenant_id)
                & Key("machine_timestamp").between(start_key, end_key)
            )
        }
        if last_key:
            params["ExclusiveStartKey"] = last_key

        response = table.query(**params)
        records.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")

        if not last_key:
            break
    return records


def get_value(record, variable):
    telemetry = record.get("telemetria", record)
    value = telemetry.get(variable)
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def calculate_trend(values):
    if len(values) < 2:
        return 0.0

    middle = len(values) // 2
    first_half = values[:middle]
    second_half = values[middle:]

    if not first_half or not second_half:
        return 0.0

    old_average = sum(first_half) / len(first_half)
    new_average = sum(second_half) / len(second_half)

    if old_average == 0:
        return 0.0

    percentage = ((new_average - old_average) / abs(old_average)) * 100
    return round(percentage, 2)


def lambda_handler(event, context):
    try:
        tenant_id = event.get("tenant_id")
        machine_id = event.get("machine_id")

        if not tenant_id or not machine_id:
            raise ValueError("Falta tenant_id o machine_id en el payload")

        analysis_window_days = int(event.get("analysis_window_days", 30))

        # Calcular periodo histórico
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=analysis_window_days)
        start_timestamp = start_time.isoformat()
        end_timestamp = end_time.isoformat()

        # Recuperar datos históricos
        telemetry_records = query_machine_records(
            telemetry_table, tenant_id, machine_id, start_timestamp, end_timestamp
        )
        anomaly_records = query_machine_records(
            anomalies_table, tenant_id, machine_id, start_timestamp, end_timestamp
        )
        incident_records = query_machine_records(
            incidents_table, tenant_id, machine_id, start_timestamp, end_timestamp
        )

        # [CORREGIDO]: Construcción de hallazgos detallados (findings), recurrencia y historical_risk_score
        # exigidos por el PDF y consumidos por EvaluarIncidente.py y RecomendarMantenimiento.py
        findings = []
        max_trend = 0.0

        if telemetry_records:
            telemetry_records.sort(key=lambda x: x.get("timestamp", ""))
            variables = ["temperature", "vibration", "pressure", "fuel_consumption"]

            for variable in variables:
                values = [get_value(record, variable) for record in telemetry_records]
                values = [v for v in values if v is not None]

                if len(values) < 2:
                    continue

                trend = calculate_trend(values)
                if trend >= 15.0:
                    findings.append(f"{variable}_increasing")
                    max_trend = max(max_trend, trend)

        similar_incidents = len(incident_records)
        previous_anomalies = len(anomaly_records)

        if previous_anomalies >= 3:
            findings.append("frequent_historical_anomalies")
        if similar_incidents >= 2:
            findings.append("recurrent_past_incidents")

        degradacion_detectada = any(f.endswith("_increasing") for f in findings)
        recurrence_detected = (
            degradacion_detectada
            or previous_anomalies >= 3
            or similar_incidents >= 2
        )
        historical_pattern_detected = len(findings) > 0

        if degradacion_detectada:
            pattern_type = "degradacion_progresiva"
        elif previous_anomalies >= 3 or similar_incidents >= 2:
            pattern_type = "picos_frecuentes"
        else:
            pattern_type = None

        # [CORREGIDO]: Cálculo cuantitativo de historical_risk_score (0 - 100)
        trend_points = min(40.0, max_trend * 1.2) if degradacion_detectada else 0.0
        anomaly_points = min(30.0, previous_anomalies * 6.0)
        incident_points = min(30.0, similar_incidents * 12.0)
        historical_risk_score = round(min(100.0, trend_points + anomaly_points + incident_points))

        historical_result = {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "historical_pattern_detected": historical_pattern_detected,
            "pattern_type": pattern_type,
            "findings": findings,
            "similar_incidents": similar_incidents,
            "previous_anomalies": previous_anomalies,
            "recurrence_detected": recurrence_detected,
            "historical_risk_score": historical_risk_score
        }

        # [CORREGIDO]: Persistencia en DynamoDB (t_analisis) y S3 según diagrama de arquitectura
        now_iso = end_timestamp
        machine_ts = f"{machine_id}#{now_iso}"
        try:
            analysis_item = {
                "tenant_id": tenant_id,
                "sk": machine_ts,
                "machine_timestamp": machine_ts,
                "analysis_id": str(uuid.uuid4()),
                "machine_id": machine_id,
                "timestamp": now_iso,
                **json.loads(json.dumps(historical_result), parse_float=Decimal)
            }
            analysis_table.put_item(Item=analysis_item)
        except Exception as db_err:
            print(f"Advertencia: no se pudo guardar en {ANALYSIS_TABLE_NAME}: {db_err}")

        if ANALYSIS_BUCKET_NAME:
            try:
                s3_key = f"{tenant_id}/{machine_id}/{now_iso}.json"
                s3.put_object(
                    Bucket=ANALYSIS_BUCKET_NAME,
                    Key=s3_key,
                    Body=json.dumps(historical_result),
                    ContentType="application/json"
                )
            except Exception as s3_err:
                print(f"Advertencia: no se pudo guardar en S3 {ANALYSIS_BUCKET_NAME}: {s3_err}")

        # [CORREGIDO]: Propaga resultados anteriores para mantener el contexto intacto en Step Functions
        anomaly_result = event.get("anomaly_result") or {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "anomaly_detected": event.get("anomaly_detected", False),
            "anomalies": event.get("anomalies", [])
        }
        pattern_result = event.get("pattern_result") or {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "pattern_detected": event.get("pattern_detected", False),
            "pattern_type": event.get("pattern_type"),
            "affected_machines": event.get("affected_machines", []),
            "confidence": event.get("confidence", 0.0)
        }

        salida = {
            **historical_result,
            "anomaly_result": anomaly_result,
            "pattern_result": pattern_result,
            "historical_result": historical_result,
            "machine_data": event.get("machine_data", {})
        }

        return salida

    except Exception as e:
        print(f"Error en analisis historico: {str(e)}")
        raise e
