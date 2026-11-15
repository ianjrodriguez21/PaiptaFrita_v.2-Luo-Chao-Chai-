
"""
Lambda: CrearIncidente

NO utiliza IA ni SageMaker.

Recibe una evaluacion ya realizada.
Registra el incidente en DynamoDB.

Tabla: t_incidentes
PK: tenant_id
SK: sk = INC#incident_id

Salida: incident_result
"""

import os
import json
import math
import hashlib
import logging
from decimal import Decimal
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")

INCIDENTS_TABLE_NAME = os.environ.get(
    "INCIDENTS_TABLE_NAME",
    "t_incidentes"
)

table = dynamodb.Table(INCIDENTS_TABLE_NAME)


def normalize_timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp obligatorio")

    date = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if date.tzinfo is None:
        raise ValueError(
            "timestamp requiere zona horaria"
        )

    return date.astimezone(
        timezone.utc
    ).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def format_result(item):
    return {
        "tenant_id": item["tenant_id"],
        "incident_id": item["incident_id"],
        "machine_id": item["machine_id"],
        "detected_at": item["detected_at"],
        "incident_type": item["incident_type"],
        "severity": item["severity"],
        "priority": item["priority"],
        "risk_score": float(item["risk_score"]),
        "maintenance_type": item.get("maintenance_type"),
        "status": item["status"]
    }


def lambda_handler(event, context):
    try:
        # 1. Recuperar contexto Step Functions
        tenant_id = event.get("tenant_id")
        machine_id = event.get("machine_id")

        timestamp = normalize_timestamp(
            event.get("timestamp")
        )

        evaluation = event.get("evaluation_result")
        maintenance = event.get("maintenance_result")

        if not tenant_id or not machine_id:
            raise ValueError(
                "tenant_id y machine_id obligatorios"
            )

        if not isinstance(evaluation, dict):
            raise ValueError(
                "Falta evaluation_result"
            )

        if not isinstance(maintenance, dict):
            raise ValueError(
                "Falta maintenance_result"
            )

        # 2. Verificar identidad
        for result in (evaluation, maintenance):
            if (
                result.get("tenant_id") != tenant_id
                or result.get("machine_id") != machine_id
            ):
                raise ValueError(
                    "Resultados de otra maquina o tenant"
                )

        # 3. Verificar decisión previa
        if evaluation.get("incident_required") is not True:
            raise ValueError(
                "La evaluacion no requiere incidente"
            )

        if maintenance.get("incident_required") is not True:
            raise ValueError(
                "Mantenimiento no requiere incidente"
            )

        state = evaluation.get("recommended_state")
        next_action = maintenance.get("next_action")

        valid_paths = {
            "warning": "schedule_maintenance",
            "critical": "urgent_intervention"
        }

        if valid_paths.get(state) != next_action:
            raise ValueError(
                "Decision de mantenimiento inconsistente"
            )

        # 4. Datos del incidente
        incident_type = evaluation.get("incident_type")
        severity = evaluation.get("severity")
        priority = evaluation.get("priority")
        risk_score = evaluation.get("risk_score")

        if not isinstance(incident_type, str) or not incident_type:
            raise ValueError("incident_type obligatorio")

        if severity not in ("low", "medium", "high", "critical"):
            raise ValueError("severity invalida")

        if priority not in ("low", "medium", "high", "urgent"):
            raise ValueError("priority invalida")

        if (
            isinstance(risk_score, bool)
            or risk_score is None
        ):
            raise ValueError("risk_score invalido")

        risk_score = float(risk_score)

        if not math.isfinite(risk_score) or not 0 <= risk_score <= 100:
            raise ValueError(
                "risk_score fuera de rango"
            )

        # 5. ID deterministico para reintentos
        event_id = event.get("event_id") or timestamp

        fingerprint = "|".join([
            tenant_id,
            machine_id,
            str(event_id)
        ])

        incident_id = (
            "INC-"
            + hashlib.sha256(
                fingerprint.encode("utf-8")
            ).hexdigest()[:16].upper()
        )

        # 6. Construir registro
        item = {
            "tenant_id": tenant_id,
            "sk": f"INC#{incident_id}",
            "incident_id": incident_id,
            "machine_id": machine_id,
            "detected_at": timestamp,
            "timestamp": timestamp,
            "machine_timestamp": (
                f"{machine_id}#{timestamp}"
            ),
            "incident_type": incident_type,
            "severity": severity,
            "priority": priority,
            "risk_score": Decimal(str(risk_score)),
            "maintenance_type": maintenance.get(
                "maintenance_type"
            ),
            "status": "open",
            "created_at": datetime.now(
                timezone.utc
            ).isoformat()
        }

        # 7. Guardar en DynamoDB
        try:
            table.put_item(
                Item=item,
                ConditionExpression=(
                    "attribute_not_exists(tenant_id) "
                    "AND attribute_not_exists(sk)"
                )
            )

        except ClientError as exc:
            if exc.response["Error"]["Code"] != (
                "ConditionalCheckFailedException"
            ):
                raise

            # Reintento de un incidente ya registrado
            existing = table.get_item(
                Key={
                    "tenant_id": tenant_id,
                    "sk": f"INC#{incident_id}"
                },
                ConsistentRead=True
            ).get("Item")

            if existing is None:
                raise RuntimeError(
                    "Incidente duplicado no encontrado"
                )

            if (
                existing.get("machine_id") != machine_id
                or existing.get("detected_at") != timestamp
            ):
                raise ValueError(
                    "Conflicto de identificador de incidente"
                )

            return format_result(existing)

        logger.info(
            "Incidente creado: %s",
            incident_id
        )

        return format_result(item)

    except Exception:
        logger.exception(
            "Error en CrearIncidente"
        )
        raise
