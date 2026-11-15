
"""
Lambda: ReservarRepuestos

t_repuestos:
    PK tenant_id, SK part_id

t_reservas_repuestos:
    PK tenant_id, SK sk

Entrada:
    incident_result
    parts_result

Salida:
    reservation_result

Reserva todos los repuestos atomicamente.
Maximo 25 tipos de pieza por ejecucion.
"""

import os
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

PARTS_TABLE = os.environ.get(
    "PARTS_TABLE_NAME", "t_repuestos"
)

RESERVATIONS_TABLE = os.environ.get(
    "RESERVATIONS_TABLE_NAME",
    "t_reservas_repuestos"
)

parts_table = db.Table(PARTS_TABLE)
reservations_table = db.Table(RESERVATIONS_TABLE)


def av(item):
    return {
        key: serializer.serialize(value)
        for key, value in item.items()
    }


def reservation_key(tenant, incident, part):
    return {
        "tenant_id": tenant,
        "sk": f"RES#{incident}#{part}"
    }


def previous_reservations(tenant, incident, requested):
    existing = []

    for part in requested:
        key = reservation_key(
            tenant, incident, part["part_id"]
        )

        item = reservations_table.get_item(
            Key=key,
            ConsistentRead=True
        ).get("Item")

        if item:
            existing.append(item)

    return existing


def format_reservation(item):
    return {
        "reservation_id": item["reservation_id"],
        "part_id": item["part_id"],
        "quantity": int(item["quantity"]),
        "reserved_at": item["reserved_at"],
        "status": item["status"]
    }


def lambda_handler(event, context):
    tenant_id = event.get("tenant_id")
    incident = event.get("incident_result") or {}
    parts_result = event.get("parts_result") or {}

    incident_id = incident.get("incident_id")

    if not tenant_id or not incident_id:
        raise ValueError("Falta tenant_id o incident_id")

    if (
        incident.get("tenant_id") != tenant_id
        or parts_result.get("tenant_id") != tenant_id
        or parts_result.get("incident_id") != incident_id
    ):
        raise ValueError("Contexto inconsistente")

    if parts_result.get("status") == "no_parts_required":
        return {
            "tenant_id": tenant_id,
            "incident_id": incident_id,
            "reservations": [],
            "all_reserved": True,
            "status": "no_parts_required"
        }

    if parts_result.get("all_parts_available") is not True:
        return {
            "tenant_id": tenant_id,
            "incident_id": incident_id,
            "reservations": [],
            "all_reserved": False,
            "status": "insufficient_stock"
        }

    requested = parts_result.get("required_parts", [])

    if not isinstance(requested, list) or not requested:
        raise ValueError(
            "No hay repuestos para reservar"
        )

    if len(requested) > 25:
        raise ValueError(
            "Maximo 25 repuestos por transaccion"
        )

    seen = set()

    for part in requested:
        if not isinstance(part, dict):
            raise ValueError("Repuesto invalido")

        part_id = part.get("part_id")
        quantity = part.get("required_quantity")

        if (
            not isinstance(part_id, str)
            or not part_id
            or type(quantity) is not int
            or quantity <= 0
            or part_id in seen
        ):
            raise ValueError(
                "IDs duplicados o cantidades invalidas"
            )

        seen.add(part_id)

    # Idempotencia: comprobar reservas anteriores
    existing = previous_reservations(
        tenant_id, incident_id, requested
    )

    if existing:
        if len(existing) != len(requested):
            raise RuntimeError(
                "Existen reservas parciales anteriores"
            )

        expected = {
            p["part_id"]: p["required_quantity"]
            for p in requested
        }

        if any(
            int(item["quantity"])
                != expected.get(item["part_id"])
            for item in existing
        ):
            raise ValueError(
                "Las cantidades reservadas no coinciden"
            )

        if any(
            item["status"] != "reserved"
            for item in existing
        ):
            raise ValueError(
                "La reserva existente ya cambio de estado"
            )

        return {
            "tenant_id": tenant_id,
            "incident_id": incident_id,
            "reservations": [
                format_reservation(item)
                for item in existing
            ],
            "all_reserved": True,
            "status": "reserved"
        }

    # Preparar transaccion
    operations = []
    reservations = []

    now = datetime.now(timezone.utc).isoformat()

    for requested_part in requested:
        part_id = requested_part["part_id"]
        quantity = requested_part["required_quantity"]

        stock_item = parts_table.get_item(
            Key={
                "tenant_id": tenant_id,
                "part_id": part_id
            },
            ConsistentRead=True
        ).get("Item")

        if not stock_item:
            return {
                "tenant_id": tenant_id,
                "incident_id": incident_id,
                "reservations": [],
                "all_reserved": False,
                "status": "insufficient_stock"
            }

        current = int(stock_item.get("quantity", 0))
        minimum = int(
            stock_item.get("minimum_stock", 0)
        )

        if current < quantity:
            return {
                "tenant_id": tenant_id,
                "incident_id": incident_id,
                "reservations": [],
                "all_reserved": False,
                "status": "insufficient_stock"
            }

        remaining = current - quantity

        if remaining == 0:
            stock_status = "out_of_stock"
        elif remaining <= minimum:
            stock_status = "low_stock"
        else:
            stock_status = "available"

        reservation_id = (
            "RES-"
            + hashlib.sha256(
                f"{tenant_id}|{incident_id}|{part_id}".encode()
            ).hexdigest()[:16].upper()
        )

        reservation = {
            **reservation_key(
                tenant_id, incident_id, part_id
            ),
            "reservation_id": reservation_id,
            "incident_id": incident_id,
            "part_id": part_id,
            "quantity": quantity,
            "reserved_at": now,
            "status": "reserved"
        }

        # Operacion 1: descontar stock
        operations.append({
            "Update": {
                "TableName": PARTS_TABLE,
                "Key": av({
                    "tenant_id": tenant_id,
                    "part_id": part_id
                }),
                "UpdateExpression": (
                    "SET #qty = :remaining, "
                    "#status = :status"
                ),
                "ConditionExpression": (
                    "attribute_exists(tenant_id) "
                    "AND attribute_exists(part_id) "
                    "AND #qty = :original"
                ),
                "ExpressionAttributeNames": {
                    "#qty": "quantity",
                    "#status": "status"
                },
                "ExpressionAttributeValues": av({
                    ":remaining": remaining,
                    ":original": current,
                    ":status": stock_status
                })
            }
        })

        # Operacion 2: guardar reserva
        operations.append({
            "Put": {
                "TableName": RESERVATIONS_TABLE,
                "Item": av(reservation),
                "ConditionExpression": (
                    "attribute_not_exists(tenant_id) "
                    "AND attribute_not_exists(sk)"
                )
            }
        })

        reservations.append(reservation)

    # 2 acciones por repuesto, 50 en total como maximo
    try:
        client.transact_write_items(
            TransactItems=operations
        )

    except ClientError as exc:
        if exc.response["Error"]["Code"] != (
            "TransactionCanceledException"
        ):
            raise

        # Reintento de la misma transaccion
        existing = previous_reservations(
            tenant_id, incident_id, requested
        )

        if len(existing) == len(requested) and all(
            item["status"] == "reserved"
            and int(item["quantity"]) == next(
                p["required_quantity"]
                for p in requested
                if p["part_id"] == item["part_id"]
            )
            for item in existing
        ):
            return {
                "tenant_id": tenant_id,
                "incident_id": incident_id,
                "reservations": [
                    format_reservation(item)
                    for item in existing
                ],
                "all_reserved": True,
                "status": "reserved"
            }

        # Conflicto concurrente: volver a consultar stock
        # mediante una nueva ejecución.
        return {
            "tenant_id": tenant_id,
            "incident_id": incident_id,
            "reservations": [],
            "all_reserved": False,
            "status": "reservation_conflict"
        }

    return {
        "tenant_id": tenant_id,
        "incident_id": incident_id,
        "reservations": [
            format_reservation(item)
            for item in reservations
        ],
        "all_reserved": True,
        "status": "reserved"
    }
