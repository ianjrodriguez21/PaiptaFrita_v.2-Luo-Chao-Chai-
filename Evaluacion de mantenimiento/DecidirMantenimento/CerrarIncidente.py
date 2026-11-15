
"""
Lambda: CerrarIncidente

Tabla provisional:
t_incidentes
    PK: tenant_id
    SK: sk = INC#<incident_id>

Cierra un incidente existente en DynamoDB.
"""

import os
import logging
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


def format_result(item):
    return {
        "tenant_id": item["tenant_id"],
        "incident_id": item["incident_id"],
        "machine_id": item["machine_id"],
        "status": item["status"],
        "resolved_at": item.get("resolved_at"),
        "resolution": item.get("resolution")
    }


def lambda_handler(event, context):
    try:
        data = event.get("Payload", event)

        tenant_id = data.get("tenant_id")
        incident_id = data.get("incident_id")
        machine_id = data.get("machine_id")
        resolution = data.get("resolution")

        if not all([
            tenant_id,
            incident_id,
            machine_id,
            resolution
        ]):
            raise ValueError(
                "tenant_id, incident_id, machine_id "
                "y resolution son obligatorios"
            )

        if not isinstance(resolution, str):
            raise ValueError(
                "resolution debe ser texto"
            )

        resolved_at = datetime.now(
            timezone.utc
        ).isoformat()

        key = {
            "tenant_id": tenant_id,
            "sk": f"INC#{incident_id}"
        }

        try:
            response = table.update_item(
                Key=key,
                UpdateExpression=(
                    "SET #status = :resolved, "
                    "resolved_at = :time, "
                    "resolution = :resolution"
                ),
                ConditionExpression=(
                    "attribute_exists(tenant_id) "
                    "AND attribute_exists(sk) "
                    "AND machine_id = :machine "
                    "AND #status IN (:open, :assigned, :progress)"
                ),
                ExpressionAttributeNames={
                    "#status": "status"
                },
                ExpressionAttributeValues={
                    ":resolved": "resolved",
                    ":time": resolved_at,
                    ":resolution": resolution.strip(),
                    ":machine": machine_id,
                    ":open": "open",
                    ":assigned": "assigned",
                    ":progress": "in_progress"
                },
                ReturnValues="ALL_NEW"
            )

            item = response["Attributes"]

        except ClientError as exc:
            error_code = exc.response.get(
                "Error", {}
            ).get("Code")

            if error_code != "ConditionalCheckFailedException":
                raise

            existing = table.get_item(
                Key=key,
                ConsistentRead=True
            ).get("Item")

            if not existing:
                raise ValueError(
                    "El incidente no existe"
                )

            if existing.get("machine_id") != machine_id:
                raise ValueError(
                    "El incidente no pertenece a esa máquina"
                )

            if existing.get("status") == "resolved":
                logger.info(
                    "El incidente %s ya estaba resuelto",
                    incident_id
                )
                return format_result(existing)

            raise ValueError(
                "El incidente no puede cerrarse "
                "desde su estado actual"
            )

        logger.info(
            "Incidente %s cerrado exitosamente",
            incident_id
        )

        return format_result(item)

    except Exception:
        logger.exception(
            "Error cerrando incidente"
        )
        raise
