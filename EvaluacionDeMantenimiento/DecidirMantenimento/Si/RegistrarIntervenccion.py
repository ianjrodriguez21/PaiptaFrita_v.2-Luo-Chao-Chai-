
"""
Lambda: RegistrarIntervenccion

t_intervenciones:
    PK tenant_id, SK sk

t_incidentes:
    PK tenant_id, SK sk

Entrada:
    incident_result
    assignment_result
    intervention_data

Acciones:
    start
    complete

Salida:
    intervention_result
"""

import os
import hashlib
import logging
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

db = boto3.resource("dynamodb")
client = boto3.client("dynamodb")
serializer = TypeSerializer()

INTERVENTIONS_TABLE = os.environ.get(
    "INTERVENTIONS_TABLE_NAME",
    "t_interventions"
)

INCIDENTS_TABLE = os.environ.get(
    "INCIDENTS_TABLE_NAME",
    "t_incidents"
)

table = db.Table(INTERVENTIONS_TABLE)


def av(item):
    return {
        key: serializer.serialize(value)
        for key, value in item.items()
    }


def parse_date(value):
    if not isinstance(value, str):
        raise ValueError("Fecha ISO 8601 obligatoria")

    date = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if date.tzinfo is None:
        raise ValueError(
            "La fecha requiere zona horaria"
        )

    return date.astimezone(timezone.utc)


def validate_parts(parts):
    if not isinstance(parts, list):
        raise ValueError("parts_used debe ser una lista")

    result = []

    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("Repuesto invalido")

        part_id = part.get("part_id")
        quantity = part.get("quantity")

        if (
            not isinstance(part_id, str)
            or not part_id
            or type(quantity) is not int
            or quantity <= 0
        ):
            raise ValueError(
                "part_id y quantity invalidos"
            )

        result.append({
            "part_id": part_id,
            "quantity": quantity
        })

    return result


def get_intervention(tenant_id, intervention_id):
    return table.get_item(
        Key={
            "tenant_id": tenant_id,
            "sk": f"INT#{intervention_id}"
        },
        ConsistentRead=True
    ).get("Item")


def output(item):
    return {
        "tenant_id": item["tenant_id"],
        "intervention_id": item["intervention_id"],
        "incident_id": item["incident_id"],
        "machine_id": item["machine_id"],
        "technician_id": item["technician_id"],
        "started_at": item["started_at"],
        "completed_at": item.get("completed_at"),
        "diagnosis": item.get("diagnosis"),
        "parts_used": item.get("parts_used", []),
        "downtime_minutes": item.get(
            "downtime_minutes"
        ),
        "result": item.get("result"),
        "status": item["status"]
    }


def lambda_handler(event, context):
    tenant_id = event.get("tenant_id")
    machine_id = event.get("machine_id")

    incident = event.get("incident_result") or {}
    assignment = event.get("assignment_result") or {}
    intervention = event.get("intervention_data") or {}

    incident_id = incident.get("incident_id")
    technician_id = assignment.get("technician_id")

    if not all([
        tenant_id,
        machine_id,
        incident_id,
        technician_id
    ]):
        raise ValueError(
            "Faltan datos del incidente o asignacion"
        )

    if (
        incident.get("tenant_id") != tenant_id
        or incident.get("machine_id") != machine_id
        or assignment.get("tenant_id") != tenant_id
        or assignment.get("incident_id") != incident_id
    ):
        raise ValueError("Contexto inconsistente")

    if assignment.get("assignment_status") != "assigned":
        raise ValueError(
            "El tecnico no esta asignado"
        )

    action = intervention.get("action")

    if action not in ("start", "complete"):
        raise ValueError(
            "intervention_data.action debe ser start o complete"
        )

    intervention_id = (
        "INT-"
        + hashlib.sha256(
            f"{tenant_id}|{incident_id}".encode()
        ).hexdigest()[:16].upper()
    )

    key = {
        "tenant_id": tenant_id,
        "sk": f"INT#{intervention_id}"
    }

    existing = get_intervention(
        tenant_id, intervention_id
    )

    # ----------------------------------
    # INICIAR INTERVENCION
    # ----------------------------------
    if action == "start":
        if existing:
            if (
                existing["incident_id"] != incident_id
                or existing["technician_id"] != technician_id
            ):
                raise ValueError(
                    "Intervencion existente inconsistente"
                )
            return output(existing)

        started_at = intervention.get("started_at")

        parse_date(started_at)

        item = {
            **key,
            "intervention_id": intervention_id,
            "incident_id": incident_id,
            "machine_id": machine_id,
            "technician_id": technician_id,
            "started_at": started_at,
            "completed_at": None,
            "diagnosis": None,
            "parts_used": [],
            "downtime_minutes": None,
            "result": None,
            "status": "in_progress"
        }

        try:
            client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": INTERVENTIONS_TABLE,
                            "Item": av(item),
                            "ConditionExpression": (
                                "attribute_not_exists(tenant_id) "
                                "AND attribute_not_exists(sk)"
                            )
                        }
                    },
                    {
                        "Update": {
                            "TableName": INCIDENTS_TABLE,
                            "Key": av({
                                "tenant_id": tenant_id,
                                "sk": f"INC#{incident_id}"
                            }),
                            "UpdateExpression": (
                                "SET #status = :progress"
                            ),
                            "ConditionExpression": (
                                "attribute_exists(tenant_id) "
                                "AND attribute_exists(sk) "
                                "AND #status = :assigned"
                            ),
                            "ExpressionAttributeNames": {
                                "#status": "status"
                            },
                            "ExpressionAttributeValues": av({
                                ":progress": "in_progress",
                                ":assigned": "assigned"
                            })
                        }
                    }
                ]
            )

        except ClientError as exc:
            if exc.response["Error"]["Code"] != (
                "TransactionCanceledException"
            ):
                raise

            previous = get_intervention(
                tenant_id, intervention_id
            )

            if previous:
                return output(previous)

            raise

        return output(item)

    # ----------------------------------
    # COMPLETAR INTERVENCION
    # ----------------------------------
    if not existing:
        raise ValueError(
            "La intervencion debe iniciarse primero"
        )

    if (
        existing["incident_id"] != incident_id
        or existing["machine_id"] != machine_id
        or existing["technician_id"] != technician_id
    ):
        raise ValueError("Intervencion inconsistente")

    if existing["status"] == "completed":
        return output(existing)

    if existing["status"] != "in_progress":
        raise ValueError(
            "Estado de intervencion invalido"
        )

    completed_at = intervention.get(
        "completed_at"
    )

    diagnosis = intervention.get("diagnosis")
    result = intervention.get("result")

    if not isinstance(diagnosis, str) or not diagnosis.strip():
        raise ValueError("diagnosis obligatorio")

    if result not in (
        "operational",
        "requires_additional_repair",
        "out_of_service"
    ):
        raise ValueError("result invalido")

    start_date = parse_date(
        existing["started_at"]
    )

    end_date = parse_date(completed_at)

    if end_date < start_date:
        raise ValueError(
            "completed_at anterior a started_at"
        )

    calculated_minutes = round(
        (end_date - start_date).total_seconds() / 60
    )

    downtime_minutes = intervention.get(
        "downtime_minutes",
        calculated_minutes
    )

    if (
        type(downtime_minutes) is not int
        or downtime_minutes < 0
    ):
        raise ValueError(
            "downtime_minutes invalido"
        )

    parts_used = validate_parts(
        intervention.get("parts_used", [])
    )

    try:
        response = table.update_item(
            Key=key,
            UpdateExpression=(
                "SET #status = :completed, "
                "completed_at = :end, "
                "diagnosis = :diagnosis, "
                "parts_used = :parts, "
                "downtime_minutes = :downtime, "
                "#result = :result"
            ),
            ConditionExpression=(
                "attribute_exists(tenant_id) "
                "AND attribute_exists(sk) "
                "AND #status = :progress "
                "AND technician_id = :technician"
            ),
            ExpressionAttributeNames={
                "#status": "status",
                "#result": "result"
            },
            ExpressionAttributeValues={
                ":completed": "completed",
                ":progress": "in_progress",
                ":end": completed_at,
                ":diagnosis": diagnosis.strip(),
                ":parts": parts_used,
                ":downtime": downtime_minutes,
                ":result": result,
                ":technician": technician_id
            },
            ReturnValues="ALL_NEW"
        )

        return output(response["Attributes"])

    except ClientError as exc:
        if exc.response["Error"]["Code"] != (
            "ConditionalCheckFailedException"
        ):
            raise

        previous = get_intervention(
            tenant_id, intervention_id
        )

        if previous and previous["status"] == "completed":
            return output(previous)

        raise
