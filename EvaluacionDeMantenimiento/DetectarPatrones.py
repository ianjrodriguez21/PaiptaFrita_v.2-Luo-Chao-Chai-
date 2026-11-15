
"""
Lambda: DetectarPatrones

Entrada:
    Contexto de Step Functions
    anomaly_result

Salida:
    pattern_result

Tabla de lectura:
    t_anomalias
    PK: tenant_id
    SK: sk

GSI requerido:
    tenant-timestamp-index
    PK: tenant_id
    SK: timestamp

Tabla de escritura:
    t_patrones
    PK: tenant_id
    SK: sk
"""

import os
import json
import math
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
sagemaker = boto3.client("sagemaker-runtime")

ALERTS_TABLE_NAME = os.environ.get(
    "ALERTS_TABLE_NAME",
    "t_anomalias"
)

PATTERNS_TABLE_NAME = os.environ.get(
    "PATTERNS_TABLE_NAME",
    "t_patrones"
)

PATTERNS_ENDPOINT = os.environ[
    "SAGEMAKER_PATTERNS_ENDPOINT"
]

INDEX_NAME = os.environ.get(
    "ALERTS_TIME_INDEX",
    "tenant-timestamp-index"
)

alerts_table = dynamodb.Table(ALERTS_TABLE_NAME)
patterns_table = dynamodb.Table(PATTERNS_TABLE_NAME)


def normalize_timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp obligatorio")

    dt = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if dt.tzinfo is None:
        raise ValueError(
            "timestamp debe incluir zona horaria"
        )

    return dt.astimezone(
        timezone.utc
    ).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def parse_timestamp(value):
    return datetime.fromisoformat(
        normalize_timestamp(value).replace(
            "Z", "+00:00"
        )
    )


def query_recent_anomalies(
    tenant_id, start_time, end_time
):
    """Consulta anomalías por tenant e intervalo."""

    records = []
    last_key = None

    while True:
        params = {
            "IndexName": INDEX_NAME,
            "KeyConditionExpression": (
                Key("tenant_id").eq(tenant_id)
                & Key("timestamp").between(
                    start_time, end_time
                )
            )
        }

        if last_key:
            params["ExclusiveStartKey"] = last_key

        response = alerts_table.query(**params)

        records.extend(
            response.get("Items", [])
        )

        last_key = response.get(
            "LastEvaluatedKey"
        )

        if not last_key:
            break

    return records


def build_instances(alerts):
    """Construye observaciones para SageMaker."""

    instances = []

    for alert in alerts:
        telemetry = alert.get(
            "telemetria", {}
        )

        fields = [
            "temperature",
            "vibration",
            "pressure"
        ]

        if any(
            telemetry.get(field) is None
            for field in fields
        ):
            continue

        try:
            features = [
                float(telemetry[field])
                for field in fields
            ]

            fuel = telemetry.get(
                "fuel_consumption", 0
            )

            features.append(
                float(fuel if fuel is not None else 0)
            )

            if not all(
                math.isfinite(value)
                for value in features
            ):
                continue

        except (ValueError, TypeError):
            continue

        instances.append({
            "machine_id": alert.get("machine_id"),
            "alert_id": alert.get("alert_id"),
            "timestamp": alert.get("timestamp"),
            "features": features
        })

    return instances


def invoke_clustering_model(instances):
    """Invoca modelo de clustering en SageMaker."""

    response = sagemaker.invoke_endpoint(
        EndpointName=PATTERNS_ENDPOINT,
        ContentType="application/json",
        Accept="application/json",
        Body=json.dumps({
            "instances": instances
        })
    )

    result = json.loads(
        response["Body"].read().decode("utf-8")
    )

    assignments = result.get("assignments")

    if not isinstance(assignments, list):
        raise ValueError(
            "SageMaker debe devolver assignments"
        )

    # Normalizar las asignaciones devueltas
    valid_ids = {
        item["alert_id"]: item["machine_id"]
        for item in instances
        if item.get("alert_id")
        and item.get("machine_id")
    }

    normalized = []

    for assignment in assignments:
        if not isinstance(assignment, dict):
            raise ValueError(
                "Asignación de clustering inválida"
            )

        alert_id = assignment.get("alert_id")
        machine_id = assignment.get("machine_id")
        cluster_id = assignment.get("cluster_id")

        if (
            alert_id not in valid_ids
            or valid_ids[alert_id] != machine_id
        ):
            raise ValueError(
                "SageMaker devolvió una alerta desconocida"
            )

        if type(cluster_id) is not int:
            raise ValueError(
                "cluster_id debe ser entero"
            )

        normalized.append({
            "alert_id": alert_id,
            "machine_id": machine_id,
            "cluster_id": cluster_id
        })

    return normalized


def lambda_handler(event, context):
    try:
        # 1. Leer contexto
        tenant_id = event.get("tenant_id")
        machine_id = event.get("machine_id")

        machine_data = event.get(
            "machine_data", {}
        )

        anomaly_result = event.get(
            "anomaly_result", {}
        )

        if not tenant_id or not machine_id:
            raise ValueError(
                "tenant_id y machine_id son obligatorios"
            )

        if not isinstance(anomaly_result, dict):
            raise ValueError(
                "anomaly_result debe ser un objeto"
            )

        timestamp = normalize_timestamp(
            event.get("timestamp")
        )

        # Ventana de análisis centrada en el evento
        window_minutes = int(
            event.get("analysis_window_minutes", 15)
        )

        if not 1 <= window_minutes <= 1440:
            raise ValueError(
                "analysis_window_minutes fuera de rango"
            )

        end_dt = parse_timestamp(timestamp)
        start_dt = end_dt - timedelta(
            minutes=window_minutes
        )

        start_timestamp = start_dt.isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z")

        affected_zone = (
            machine_data.get("zone")
            or machine_data.get("affected_zone")
        )

        result = {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "timestamp": timestamp,
            "pattern_detected": False,
            "pattern_type": None,
            "affected_machines": [],
            "affected_zone": affected_zone,
            "confidence": 0.0
        }

        # 2. Consultar anomalías
        alerts = query_recent_anomalies(
            tenant_id,
            start_timestamp,
            timestamp
        )

        # Incluir anomalía actual si el GSI aún
        # no refleja su escritura reciente.
        if anomaly_result.get("anomaly_detected"):
            current_alert_id = anomaly_result.get(
                "alert_id"
            )

            existing_ids = {
                alert.get("alert_id")
                for alert in alerts
            }

            if (
                current_alert_id
                and current_alert_id not in existing_ids
            ):
                alerts.append({
                    "tenant_id": tenant_id,
                    "machine_id": machine_id,
                    "alert_id": current_alert_id,
                    "timestamp": timestamp,
                    "telemetria": machine_data
                })

        instances = build_instances(alerts)

        # Necesitamos al menos dos máquinas
        distinct_machines = {
            item["machine_id"]
            for item in instances
        }

        if len(distinct_machines) < 2:
            return result

        # 3. Invocar SageMaker
        assignments = invoke_clustering_model(
            instances
        )

        # 4. Agrupar por cluster
        clusters = {}

        for assignment in assignments:
            cluster_id = assignment["cluster_id"]

            # -1 significa ruido en DBSCAN
            if cluster_id == -1:
                continue

            clusters.setdefault(
                cluster_id, []
            ).append(assignment)

        # 5. Guardar patrones encontrados
        for cluster_id, members in clusters.items():

            machine_ids = sorted({
                member["machine_id"]
                for member in members
            })

            if len(machine_ids) < 2:
                continue

            alert_ids = sorted({
                member["alert_id"]
                for member in members
            })

            # Identificador determinístico
            fingerprint = "|".join([
                tenant_id,
                timestamp,
                str(cluster_id),
                ",".join(alert_ids)
            ])

            pattern_id = (
                "PAT-"
                + hashlib.sha256(
                    fingerprint.encode("utf-8")
                ).hexdigest()[:16].upper()
            )

            # Puntaje heurístico provisional.
            # No representa probabilidad estadística.
            confidence = round(
                min(
                    0.99,
                    0.70 + 0.05 * len(machine_ids)
                ),
                2
            )

            pattern = {
                "tenant_id": tenant_id,
                "sk": pattern_id,
                "pattern_id": pattern_id,
                "cluster_id": cluster_id,
                "pattern_type": (
                    "shared_anomalous_behavior"
                ),
                "machine_ids": machine_ids,
                "affected_zone": affected_zone,
                "confidence": Decimal(
                    str(confidence)
                ),
                "alert_ids": alert_ids,
                "machines_affected": len(machine_ids),
                "start_time": start_timestamp,
                "end_time": timestamp,
                "detected_at": datetime.now(
                    timezone.utc
                ).isoformat()
            }

            try:
                patterns_table.put_item(
                    Item=pattern,
                    ConditionExpression=(
                        "attribute_not_exists(tenant_id) "
                        "AND attribute_not_exists(sk)"
                    )
                )

            except ClientError as exc:
                if exc.response.get(
                    "Error", {}
                ).get("Code") != (
                    "ConditionalCheckFailedException"
                ):
                    raise

                logger.info(
                    "Patrón ya registrado: %s",
                    pattern_id
                )

            # 6. Reportar patrones que afectan
            # a la máquina actualmente evaluada
            if machine_id in machine_ids:
                if (
                    not result["pattern_detected"]
                    or confidence > result["confidence"]
                ):
                    result.update({
                        "pattern_detected": True,
                        "pattern_type": (
                            "shared_anomalous_behavior"
                        ),
                        "affected_machines": machine_ids,
                        "affected_zone": affected_zone,
                        "confidence": confidence
                    })

        return result

    except Exception:
        logger.exception(
            "Error detectando patrones"
        )
        raise
