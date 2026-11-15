
"""
Lambda: ActualizarEstado

Tabla: t_maquina
PK: tenant_id
SK: machine_id

Entrada: contexto de Step Functions
Salida: status_result
"""

import os
import logging
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")

MACHINES_TABLE_NAME = os.environ.get(
    "MACHINES_TABLE_NAME", "t_maquina"
)

table = dynamodb.Table(MACHINES_TABLE_NAME)


def lambda_handler(event, context):
    tenant_id = event.get("tenant_id")
    machine_id = event.get("machine_id")
    timestamp = event.get("timestamp")

    evaluation = event.get("evaluation_result")
    maintenance = event.get("maintenance_result")

    if not all([tenant_id, machine_id, timestamp]):
        raise ValueError(
            "tenant_id, machine_id y timestamp son obligatorios"
        )

    if not isinstance(evaluation, dict):
        raise ValueError("Falta evaluation_result")

    if not isinstance(maintenance, dict):
        raise ValueError("Falta maintenance_result")

    if (
        evaluation.get("recommended_state") != "normal"
        or maintenance.get("next_action") != "normal_operation"
        or maintenance.get("maintenance_recommended") is not False
    ):
        raise ValueError(
            "Esta Lambda solo permite operacion normal"
        )

    risk_score = float(evaluation["risk_score"])

    if not 0 <= risk_score < 30:
        raise ValueError("risk_score incompatible")

    updated_at = datetime.now(
        timezone.utc
    ).isoformat()

    try:
        table.update_item(
            Key={
                "tenant_id": tenant_id,
                "machine_id": machine_id
            },
            UpdateExpression=(
                "SET last_evaluation = :evaluation, "
                "last_evaluated_at = :evaluated_at"
            ),
            ConditionExpression=(
                "attribute_exists(tenant_id) "
                "AND attribute_exists(machine_id) "
                "AND #ts = :event_ts"
            ),
            ExpressionAttributeNames={
                "#ts": "timestamp"
            },
            ExpressionAttributeValues={
                ":evaluation": {
                    "risk_score": Decimal(str(risk_score)),
                    "recommended_state": "normal",
                    "maintenance_recommended": False,
                    "evaluated_at": timestamp
                },
                ":evaluated_at": updated_at,
                ":event_ts": timestamp
            }
        )

        return {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "timestamp": timestamp,
            "current_status": "operational",
            "risk_score": risk_score,
            "status_updated": True,
            "updated_at": updated_at,
            "status": "evaluated"
        }

    except ClientError as exc:
        if exc.response["Error"]["Code"] != (
            "ConditionalCheckFailedException"
        ):
            raise

        # Evaluación antigua o máquina inexistente
        return {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "timestamp": timestamp,
            "status_updated": False,
            "status": "stale_or_missing"
        }
