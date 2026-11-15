
"""
Lambda: BuscarRepuestos

Tabla: t_repuestos
PK: tenant_id
SK: part_id

Entrada:
    incident_result
    required_parts

Salida:
    parts_result

No reserva ni descuenta stock.
"""

import os
import logging
from decimal import Decimal

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

db = boto3.resource("dynamodb")

TABLE_NAME = os.environ.get(
    "PARTS_TABLE_NAME", "t_repuestos"
)

table = db.Table(TABLE_NAME)


def positive_integer(value):
    if isinstance(value, bool):
        raise ValueError("Cantidad invalida")

    try:
        n = Decimal(str(value))
    except Exception as exc:
        raise ValueError("Cantidad invalida") from exc

    if (
        not n.is_finite()
        or n <= 0
        or n != n.to_integral_value()
    ):
        raise ValueError(
            "Cantidad debe ser entero positivo"
        )

    return int(n)


def lambda_handler(event, context):
    tenant_id = event.get("tenant_id")
    incident = event.get("incident_result") or {}

    incident_id = incident.get("incident_id")

    if not tenant_id or not incident_id:
        raise ValueError("Falta tenant_id o incident_id")

    if incident.get("tenant_id") != tenant_id:
        raise ValueError("Tenant del incidente no coincide")

    required_parts = event.get("required_parts")

    if required_parts is None:
        return {
            "tenant_id": tenant_id,
            "incident_id": incident_id,
            "required_parts": [],
            "missing_parts": [],
            "all_parts_available": False,
            "status": "parts_not_defined"
        }

    if not isinstance(required_parts, list):
        raise ValueError("required_parts debe ser lista")

    if not required_parts:
        return {
            "tenant_id": tenant_id,
            "incident_id": incident_id,
            "required_parts": [],
            "missing_parts": [],
            "all_parts_available": True,
            "status": "no_parts_required"
        }

    # Consolidar IDs repetidos
    consolidated = {}

    for part in required_parts:
        if not isinstance(part, dict):
            raise ValueError("Repuesto invalido")

        part_id = part.get("part_id")
        quantity = positive_integer(
            part.get(
                "quantity",
                part.get("required_quantity")
            )
        )

        if not isinstance(part_id, str) or not part_id:
            raise ValueError("part_id obligatorio")

        consolidated[part_id] = (
            consolidated.get(part_id, 0)
            + quantity
        )

    results = []
    missing = []

    for part_id, quantity in consolidated.items():
        item = table.get_item(
            Key={
                "tenant_id": tenant_id,
                "part_id": part_id
            },
            ConsistentRead=True
        ).get("Item")

        available_quantity = (
            int(item.get("quantity", 0))
            if item else 0
        )

        available_quantity = max(
            0, available_quantity
        )

        # low_stock también puede utilizarse
        # si todavía hay cantidad suficiente.
        available = (
            item is not None
            and item.get("status") != "out_of_stock"
            and available_quantity >= quantity
        )

        result = {
            "part_id": part_id,
            "name": item.get("name") if item else None,
            "required_quantity": quantity,
            "available_quantity": available_quantity,
            "warehouse_location": (
                item.get("warehouse_location")
                if item else None
            ),
            "available": available
        }

        results.append(result)

        if not available:
            missing.append({
                "part_id": part_id,
                "required_quantity": quantity,
                "available_quantity": available_quantity,
                "shortage": max(
                    0, quantity - available_quantity
                )
            })

    all_available = not missing

    return {
        "tenant_id": tenant_id,
        "incident_id": incident_id,
        "required_parts": results,
        "missing_parts": missing,
        "all_parts_available": all_available,
        "status": (
            "available"
            if all_available
            else "insufficient_stock"
        )
    }
