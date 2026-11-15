
"""
Lambda: AsignarTecnico

t_tecnicos:
    PK tenant_id, SK technician_id

t_asignaciones:
    PK tenant_id, SK sk

t_incidentes:
    PK tenant_id, SK sk

Salida: assignment_result
"""

import os
import math
import hashlib
import logging
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

db = boto3.resource("dynamodb")
client = boto3.client("dynamodb")
serializer = TypeSerializer()

TECHNICIANS_TABLE = os.environ.get(
    "TECHNICIANS_TABLE_NAME", "t_technicians"
)
ASSIGNMENTS_TABLE = os.environ.get(
    "ASSIGNMENTS_TABLE_NAME", "t_assignments"
)
INCIDENTS_TABLE = os.environ.get(
    "INCIDENTS_TABLE_NAME", "t_incidents"
)

assignments = db.Table(ASSIGNMENTS_TABLE)


def av(item):
    return {
        k: serializer.serialize(v)
        for k, v in item.items()
    }


def previous_assignment(tenant_id, incident_id):
    return assignments.get_item(
        Key={
            "tenant_id": tenant_id,
            "sk": f"ASG#{incident_id}"
        },
        ConsistentRead=True
    ).get("Item")


def output(item):
    return {
        "tenant_id": item["tenant_id"],
        "incident_id": item["incident_id"],
        "assignment_id": item["assignment_id"],
        "technician_id": item["technician_id"],
        "assigned_at": item["assigned_at"],
        "distance_km": float(item["distance_km"]),
        "assignment_status": item["assignment_status"]
    }


def lambda_handler(event, context):
    tenant_id = event.get("tenant_id")
    incident = event.get("incident_result") or {}
    technicians = event.get("technicians_result") or {}

    incident_id = incident.get("incident_id")

    if not tenant_id or not incident_id:
        raise ValueError("Falta tenant_id o incident_id")

    if (
        incident.get("tenant_id") != tenant_id
        or technicians.get("tenant_id") != tenant_id
        or technicians.get("incident_id") != incident_id
    ):
        raise ValueError("Contexto inconsistente")

    previous = previous_assignment(
        tenant_id, incident_id
    )

    if previous:
        return output(previous)

    candidates = technicians.get(
        "available_technicians", []
    )

    if not isinstance(candidates, list):
        raise ValueError(
            "available_technicians invalido"
        )

    valid = []

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue

        if candidate.get("specialty_match") is not True:
            continue

        tech_id = candidate.get("technician_id")

        try:
            distance = float(candidate["distance_km"])
        except (ValueError, TypeError, KeyError):
            continue

        if (
            not tech_id
            or not math.isfinite(distance)
            or distance < 0
        ):
            continue

        valid.append({
            "technician_id": tech_id,
            "distance_km": distance
        })

    valid.sort(
        key=lambda x: (
            x["distance_km"],
            x["technician_id"]
        )
    )

    assignment_id = (
        "ASG-"
        + hashlib.sha256(
            f"{tenant_id}|{incident_id}".encode()
        ).hexdigest()[:16].upper()
    )

    for candidate in valid:
        tech_id = candidate["technician_id"]
        now = datetime.now(timezone.utc).isoformat()

        item = {
            "tenant_id": tenant_id,
            "sk": f"ASG#{incident_id}",
            "assignment_id": assignment_id,
            "incident_id": incident_id,
            "technician_id": tech_id,
            "assigned_at": now,
            "distance_km": Decimal(
                str(candidate["distance_km"])
            ),
            "assignment_status": "assigned"
        }

        try:
            client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": TECHNICIANS_TABLE,
                            "Key": av({
                                "tenant_id": tenant_id,
                                "technician_id": tech_id
                            }),
                            "UpdateExpression": (
                                "SET #availability = :assigned, "
                                "assigned_incident_id = :incident, "
                                "assigned_at = :now"
                            ),
                            "ConditionExpression": (
                                "attribute_exists(tenant_id) "
                                "AND attribute_exists(technician_id) "
                                "AND #availability = :available"
                            ),
                            "ExpressionAttributeNames": {
                                "#availability": "availability"
                            },
                            "ExpressionAttributeValues": av({
                                ":assigned": "assigned",
                                ":available": "available",
                                ":incident": incident_id,
                                ":now": now
                            })
                        }
                    },
                    {
                        "Put": {
                            "TableName": ASSIGNMENTS_TABLE,
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
                                "SET #status = :assigned"
                            ),
                            "ConditionExpression": (
                                "attribute_exists(tenant_id) "
                                "AND attribute_exists(sk) "
                                "AND #status = :open"
                            ),
                            "ExpressionAttributeNames": {
                                "#status": "status"
                            },
                            "ExpressionAttributeValues": av({
                                ":assigned": "assigned",
                                ":open": "open"
                            })
                        }
                    }
                ]
            )

            return output(item)

        except ClientError as exc:
            if exc.response["Error"]["Code"] != (
                "TransactionCanceledException"
            ):
                raise

            previous = previous_assignment(
                tenant_id, incident_id
            )

            if previous:
                return output(previous)

            reasons = exc.response.get(
                "CancellationReasons", []
            )

            # Solo buscar otro tecnico cuando
            # la condicion del tecnico falle.
            if (
                len(reasons) >= 3
                and reasons[0].get("Code")
                    == "ConditionalCheckFailed"
                and reasons[1].get("Code") == "None"
                and reasons[2].get("Code") == "None"
            ):
                continue

            raise

    return {
        "tenant_id": tenant_id,
        "incident_id": incident_id,
        "technician_id": None,
        "assignment_status": "unassigned",
        "reason": "No compatible technician available"
    }
