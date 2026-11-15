
"""
Lambda: DetectarAnomalias

Entrada: contexto de Step Functions con machine_data
Salida: resultado de detección de anomalías

Tabla DynamoDB:
t_anomalias
    PK: tenant_id
    SK: sk

GSI para DetectarPatrones:
    tenant-timestamp-index
    PK: tenant_id
    SK: timestamp
"""

import os
import json
import math
import logging
import hashlib
from decimal import Decimal
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

sagemaker = boto3.client("sagemaker-runtime")
dynamodb = boto3.resource("dynamodb")

SAGEMAKER_ENDPOINT = os.environ["SAGEMAKER_ENDPOINT"]

ALERTS_TABLE_NAME = os.environ.get(
    "ALERTS_TABLE_NAME",
    "t_anomalias"
)

table = dynamodb.Table(ALERTS_TABLE_NAME)

THRESHOLDS = {
    "temperature": 95.0,
    "vibration": 7.0,
    "pressure": 200.0,
    "fuel_consumption": 30.0
}


def normalize_timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp es obligatorio")

    dt = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if dt.tzinfo is None:
        raise ValueError(
            "timestamp necesita zona horaria"
        )

    return dt.astimezone(
        timezone.utc
    ).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def get_number(data, field):
    value = data.get(field)

    if value is None or isinstance(value, bool):
        raise ValueError(
            f"Campo numérico obligatorio: {field}"
        )

    number = float(value)

    if not math.isfinite(number):
        raise ValueError(
            f"Valor inválido para {field}"
        )

    return number


def identify_anomalies(telemetry, score):
    """Identifica variables que exceden umbrales."""

    anomalies = []

    for variable, threshold in THRESHOLDS.items():
        value = telemetry.get(variable)

        if value is None:
            continue

        value = get_number(telemetry, variable)

        if value > threshold:
            anomalies.append({
                "variable": variable,
                "value": value,
                "anomaly_score": score
            })

    if not anomalies:
        anomalies.append({
            "variable": "multivariate_behavior",
            "value": None,
            "anomaly_score": score
        })

    return anomalies


def lambda_handler(event, context):
    try:
        # 1. Leer contexto de Step Functions
        telemetry = event.get("machine_data")

        if not isinstance(telemetry, dict):
            raise ValueError(
                "machine_data es obligatorio"
            )

        tenant_id = event.get("tenant_id")
        machine_id = event.get("machine_id")

        if not tenant_id or not machine_id:
            raise ValueError(
                "tenant_id y machine_id son obligatorios"
            )

        if (
            telemetry.get("tenant_id") != tenant_id
            or telemetry.get("machine_id") != machine_id
        ):
            raise ValueError(
                "machine_data no coincide con el contexto"
            )

        timestamp = normalize_timestamp(
            event.get("timestamp")
        )

        # 2. Preparar variables para el modelo ML
        features = [
            get_number(telemetry, "temperature"),
            get_number(telemetry, "vibration"),
            get_number(telemetry, "pressure")
        ]

        payload = {
            "features": features
        }

        # 3. Invocar SageMaker
        response = sagemaker.invoke_endpoint(
            EndpointName=SAGEMAKER_ENDPOINT,
            ContentType="application/json",
            Accept="application/json",
            Body=json.dumps(payload)
        )

        prediction = json.loads(
            response["Body"].read().decode("utf-8")
        )

        predicted_class = prediction.get("prediction")

        if (
            type(predicted_class) is not int
            or predicted_class not in (-1, 1)
        ):
            raise ValueError(
                "SageMaker debe devolver prediction -1 o 1"
            )

        is_anomaly = predicted_class == -1

        raw_score = float(
            prediction.get("anomaly_score", 0.0)
        )

        if not math.isfinite(raw_score):
            raise ValueError(
                "anomaly_score inválido"
            )

        # Se conserva la magnitud del score.
        # No se asume que sea una probabilidad.
        anomaly_score = round(
            abs(raw_score), 4
        )

        anomalies = (
            identify_anomalies(
                telemetry, anomaly_score
            )
            if is_anomaly else []
        )

        alert_id = None

        # 4. Persistir anomalía
        if is_anomaly:
            fingerprint = (
                f"{tenant_id}|{machine_id}|{timestamp}"
            )

            alert_id = (
                "ALT-"
                + hashlib.sha256(
                    fingerprint.encode("utf-8")
                ).hexdigest()[:16].upper()
            )

            machine_timestamp = (
                f"{machine_id}#{timestamp}"
            )

            alert = {
                "tenant_id": tenant_id,
                "sk": machine_timestamp,
                "machine_timestamp": machine_timestamp,
                "alert_id": alert_id,
                "machine_id": machine_id,
                "timestamp": timestamp,
                "tipo": "ANOMALIA_DETECTADA",
                "telemetria": json.loads(
                    json.dumps(telemetry),
                    parse_float=Decimal
                ),
                "anomalies": json.loads(
                    json.dumps(anomalies),
                    parse_float=Decimal
                )
            }

            try:
                table.put_item(
                    Item=alert,
                    ConditionExpression=(
                        "attribute_not_exists(tenant_id) "
                        "AND attribute_not_exists(sk)"
                    )
                )

            except ClientError as exc:
                code = exc.response.get(
                    "Error", {}
                ).get("Code")

                if code != "ConditionalCheckFailedException":
                    raise

                # Ya se registró esta anomalía.
                logger.info(
                    "Anomalía ya registrada: %s",
                    alert_id
                )

        # 5. Salida exclusiva de esta Lambda
        return {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "timestamp": timestamp,
            "anomaly_detected": is_anomaly,
            "anomaly_score": anomaly_score,
            "alert_id": alert_id,
            "anomalies": anomalies
        }

    except Exception:
        logger.exception(
            "Error detectando anomalías"
        )
        raise
