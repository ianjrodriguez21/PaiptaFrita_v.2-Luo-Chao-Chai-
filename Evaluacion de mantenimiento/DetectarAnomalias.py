import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
import boto3

# Clientes AWS
sagemaker_runtime = boto3.client("sagemaker-runtime")
dynamodb = boto3.resource("dynamodb")

# Variables de entorno (alineadas con diagrama: t_anomalias)
ENDPOINT_NAME = os.environ["SAGEMAKER_ENDPOINT"]
ALERTS_TABLE_NAME = os.environ.get("ALERTS_TABLE_NAME", "t_anomalias")


def evaluate_anomalous_variables(telemetry, anomaly_score):
    """Identifica qué variables específicas superan umbrales operativos."""
    thresholds = {
        "temperature": 95.0,
        "vibration": 7.0,
        "pressure": 200.0,
        "fuel_consumption": 30.0
    }
    detected = []
    score = round(abs(float(anomaly_score)), 4)

    for var, limit in thresholds.items():
        val = telemetry.get(var)
        if val is not None and float(val) > limit:
            detected.append({
                "variable": var,
                "value": float(val),
                "anomaly_score": score
            })

    # Si SageMaker detectó anomalía multivariable pero ninguna superó el umbral individual
    if not detected:
        detected.append({
            "variable": "multivariate_behavior",
            "value": float(telemetry.get("temperature", 0)),
            "anomaly_score": score
        })

    return detected


def lambda_handler(event, context):
    try:
        # [CORREGIDO]: Soporta invocación por API Gateway (body), Step Functions anidado (telemetry) o JSON directo
        if isinstance(event.get("body"), str):
            telemetry = json.loads(event["body"])
        elif isinstance(event.get("body"), dict):
            telemetry = event["body"]
        elif isinstance(event.get("telemetry"), dict):
            telemetry = event["telemetry"]
        else:
            telemetry = event

        tenant_id = telemetry.get("tenant_id") or event.get("tenant_id")
        machine_id = telemetry.get("machine_id") or event.get("machine_id")

        if not tenant_id or not machine_id:
            raise ValueError("Falta tenant_id o machine_id")

        # Extraer variables para SageMaker (modelo de anomalías)
        features = [
            float(telemetry.get("temperature", 0)),
            float(telemetry.get("vibration", 0)),
            float(telemetry.get("pressure", 0))
        ]

        payload = {"features": features}

        response = sagemaker_runtime.invoke_endpoint(
            EndpointName=ENDPOINT_NAME,
            ContentType="application/json",
            Accept="application/json",
            Body=json.dumps(payload)
        )

        prediction = json.loads(response["Body"].read().decode("utf-8"))

        # El modelo devuelve: -1 = Anomalía, 1 = Normal
        is_anomaly = prediction.get("prediction") == -1
        anomaly_score = prediction.get("anomaly_score", 0.0)

        timestamp = telemetry.get("timestamp", datetime.now(timezone.utc).isoformat())

        # Construir salida según contrato del PDF
        anomalies = evaluate_anomalous_variables(telemetry, anomaly_score) if is_anomaly else []

        # Registrar anomalía en DynamoDB (t_anomalias) si corresponde
        if is_anomaly:
            table = dynamodb.Table(ALERTS_TABLE_NAME)
            machine_ts = f"{machine_id}#{timestamp}"

            # Mantiene 'sk' y 'machine_timestamp' para soportar los GSI de DetectarPatrones y AnalisisHistorico
            alert = {
                "tenant_id": tenant_id,
                "sk": machine_ts,
                "machine_timestamp": machine_ts,
                "alert_id": str(uuid.uuid4()),
                "machine_id": machine_id,
                "timestamp": timestamp,
                "tipo": "ANOMALIA_DETECTADA",
                "telemetria": json.loads(json.dumps(telemetry), parse_float=Decimal),
                "anomalies": json.loads(json.dumps(anomalies), parse_float=Decimal)
            }
            table.put_item(Item=alert)

        # [CORREGIDO]: Preserva los metadatos originales de la máquina (operating_hours, machine_type, etc.)
        # para que RecomendarMantenimiento no pierda esos datos si Step Functions encadena sin ResultPath
        machine_data = {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "machine_type": telemetry.get("machine_type", "unknown"),
            "operating_hours": telemetry.get("operating_hours", 0),
            "operating_hours_since_last_maintenance": telemetry.get(
                "operating_hours_since_last_maintenance",
                telemetry.get("operating_hours", 0)
            ),
            "latitude": telemetry.get("latitude"),
            "longitude": telemetry.get("longitude"),
            "zone": telemetry.get("zone") or telemetry.get("affected_zone", "ZONE-A")
        }

        salida = {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "timestamp": timestamp,
            "anomaly_detected": is_anomaly,
            "anomalies": anomalies,
            "machine_data": machine_data
        }

        return salida

    except Exception as e:
        print(f"Error detectando anomalias: {str(e)}")
        raise e
