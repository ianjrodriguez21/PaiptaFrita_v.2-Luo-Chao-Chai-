
"""
Lambda: AsignarTecnico

Tablas provisionales:
- t_tecnicos:
    PK: tenant_id
    SK: technician_id
- t_asignaciones:
    PK: tenant_id
    SK: sk

Recibe candidatos de TecnicosDisponibles.
Asigna el técnico más cercano disponible.
"""

import os
import hashlib
import logging
import math
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
client = boto3.client("dynamodb")
serializer = TypeSerializer()

TECHNICIANS_TABLE = os.environ.get(
    "TECHNICIANS_TABLE_NAME", "t_tecnicos"
)

ASSIGNMENTS_TABLE = os.environ.get(
    "ASSIGNMENTS_TABLE_NAME", "t_asignaciones"
)

assignments_table = dynamodb.Table(ASSIGNMENTS_TABLE)


def serialize_item(item):
    """Convierte un diccionario al formato DynamoDB."""
    return {
        key: serializer.serialize(value)
        for key, value in item.items()
    }


def existing_assignment(tenant_id, incident_id):
    response = assignments_table.get_item(
        Key={
            "tenant_id": tenant_id,
            "sk": f"ASG#{incident_id}"
        },
        ConsistentRead=True
    )
    return response.get("Item")


def format_assignment(item):
    return {
        "tenant_id": item["tenant_id"],
        "assignment_id": item["assignment_id"],
        "incident_id": item["incident_id"],
        "technician_id": item["technician_id"],
        "assigned_at": item["assigned_at"],
        "distance_km": float(item["distance_km"]),
        "assignment_status": item["assignment_status"]
    }


def lambda_handler(event, context):
    try:
        data = event.get("Payload", event)

        tenant_id = data.get("tenant_id")
        incident_id = data.get("incident_id")

        candidates = data.get(
            "available_technicians", []
        )

        if not tenant_id or not incident_id:
            raise ValueError(
                "tenant_id e incident_id son obligatorios"
            )

        if not isinstance(candidates, list):
            raise ValueError(
                "available_technicians debe ser una lista"
            )

        # Comprobar si ya existe una asignación
        previous = existing_assignment(
            tenant_id, incident_id
        )

        if previous:
            return format_assignment(previous)

        valid_candidates = []

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue

            tech_id = candidate.get("technician_id")

            if (
                not tech_id
                or candidate.get("specialty_match") is not True
            ):
                continue

            try:
                distance = float(
                    candidate.get("distance_km")
                )
            except (TypeError, ValueError):
                continue

            if not math.isfinite(distance) or distance < 0:
                continue

            valid_candidates.append({
                "technician_id": tech_id,
                "distance_km": distance
            })

        # Priorizar el más cercano
        valid_candidates.sort(
            key=lambda t: (
                t["distance_km"],
                t["technician_id"]
            )
        )

        assignment_id = (
            "ASG-"
            + hashlib.sha256(
                f"{tenant_id}|{incident_id}".encode()
            ).hexdigest()[:16].upper()
        )

        for candidate in valid_candidates:

            technician_id = candidate["technician_id"]
            assigned_at = datetime.now(
                timezone.utc
            ).isoformat()

            item = {
                "tenant_id": tenant_id,
                "sk": f"ASG#{incident_id}",
                "assignment_id": assignment_id,
                "incident_id": incident_id,
                "technician_id": technician_id,
                "assigned_at": assigned_at,
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
                                "Key": serialize_item({
                                    "tenant_id": tenant_id,
                                    "technician_id": technician_id
                                }),
                                "UpdateExpression": (
                                    "SET #availability = :assigned, "
                                    "assigned_incident_id = :incident, "
                                    "assigned_at = :time"
                                ),
                                "ConditionExpression": (
                                    "attribute_exists(tenant_id) "
                                    "AND attribute_exists(technician_id) "
                                    "AND #availability = :available"
                                ),
                                "ExpressionAttributeNames": {
                                    "#availability": "availability"
                                },
                                "ExpressionAttributeValues": (
                                    serialize_item({
                                        ":assigned": "assigned",
                                        ":available": "available",
                                        ":incident": incident_id,
                                        ":time": assigned_at
                                    })
                                )
                            }
                        },
                        {
                            "Put": {
                                "TableName": ASSIGNMENTS_TABLE,
                                "Item": serialize_item(item),
                                "ConditionExpression": (
                                    "attribute_not_exists(tenant_id) "
                                    "AND attribute_not_exists(sk)"
                                )
                            }
                        }
                    ]
                )

                logger.info(
                    "Técnico %s asignado al incidente %s",
                    technician_id,
                    incident_id
                )

                return format_assignment(item)

            except ClientError as exc:
                code = exc.response.get(
                    "Error", {}
                ).get("Code")

                if code != "TransactionCanceledException":
                    raise

                # Si otro proceso asignó este incidente
                # durante la transacción, recuperar resultado.
                previous = existing_assignment(
                    tenant_id, incident_id
                )

                if previous:
                    return format_assignment(previous)

                reasons = exc.response.get(
                    "CancellationReasons", []
                )

                conditional_failure = any(
                    reason.get("Code")
                    == "ConditionalCheckFailed"
                    for reason in reasons
                )

                if conditional_failure:
                    # Otro proceso tomó este técnico.
                    # Intentar con el siguiente candidato.
                    continue

                # Otro error transaccional: no ocultarlo.
                raise

        # Ningún técnico pudo asignarse.
        # La orquestación decidirá reintentar o notificar.
        return {
            "tenant_id": tenant_id,
            "incident_id": incident_id,
            "assignment_status": "unassigned",
            "technician_id": None,
            "reason": "No available compatible technician"
        }

    except Exception:
        logger.exception("Error al asignar técnico")
        raise
