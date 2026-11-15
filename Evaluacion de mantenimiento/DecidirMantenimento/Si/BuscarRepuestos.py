
"""
Lambda: BuscarRepuestos

Tabla provisional:
t_repuestos
    PK: tenant_id
    SK: part_id

Busca repuestos y comprueba su disponibilidad.
No modifica el inventario.
"""

import os
import logging
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")

PARTS_TABLE_NAME = os.environ.get(
    "PARTS_TABLE_NAME",
    "t_repuestos"
)

table = dynamodb.Table(PARTS_TABLE_NAME)


def validate_quantity(value):
    """Verifica que una cantidad sea entera y positiva."""
    if isinstance(value, bool):
        raise ValueError("La cantidad debe ser un entero positivo")

    try:
        quantity = Decimal(str(value))
    except Exception as exc:
        raise ValueError("Cantidad inválida") from exc

    if (
        not quantity.is_finite()
        or quantity != quantity.to_integral_value()
        or quantity <= 0
    ):
        raise ValueError("La cantidad debe ser un entero positivo")

    return int(quantity)


def lambda_handler(event, context):
    try:
        data = event.get("Payload", event)

        tenant_id = data.get("tenant_id")
        incident_id = data.get("incident_id")

        required_parts = data.get("required_parts")

        if not tenant_id or not incident_id:
            raise ValueError(
                "tenant_id e incident_id son obligatorios"
            )

        if not isinstance(required_parts, list):
            raise ValueError(
                "required_parts debe ser una lista"
            )

        if not required_parts:
            return {
                "tenant_id": tenant_id,
                "incident_id": incident_id,
                "required_parts": [],
                "all_parts_available": True,
                "missing_parts": [],
                "status": "no_parts_required"
            }

        # Unificar repuestos repetidos en la solicitud
        consolidated = {}

        for part in required_parts:
            if not isinstance(part, dict):
                raise ValueError(
                    "Cada repuesto debe ser un objeto JSON"
                )

            part_id = part.get("part_id")

            if not isinstance(part_id, str) or not part_id:
                raise ValueError(
                    "Cada repuesto necesita part_id"
                )

            quantity = validate_quantity(
                part.get("quantity", part.get("required_quantity"))
            )

            consolidated[part_id] = (
                consolidated.get(part_id, 0) + quantity
            )

        results = []
        missing_parts = []

        for part_id, required_quantity in consolidated.items():

            response = table.get_item(
                Key={
                    "tenant_id": tenant_id,
                    "part_id": part_id
                },
                ConsistentRead=True
            )

            part = response.get("Item")

            if part is None:
                result = {
                    "part_id": part_id,
                    "name": None,
                    "required_quantity": required_quantity,
                    "available_quantity": 0,
                    "warehouse_location": None,
                    "available": False
                }

            else:
                available_quantity = int(
                    part.get("quantity", 0)
                )

                available_quantity = max(
                    0, available_quantity
                )

                stock_available = (
                    part.get("status") == "available"
                    and available_quantity >= required_quantity
                )

                result = {
                    "part_id": part_id,
                    "name": part.get("name"),
                    "required_quantity": required_quantity,
                    "available_quantity": available_quantity,
                    "warehouse_location": part.get(
                        "warehouse_location"
                    ),
                    "available": stock_available
                }

            results.append(result)

            if not result["available"]:
                missing_parts.append({
                    "part_id": part_id,
                    "required_quantity": required_quantity,
                    "available_quantity": result[
                        "available_quantity"
                    ],
                    "shortage": max(
                        0,
                        required_quantity
                        - result["available_quantity"]
                    )
                })

        all_available = len(missing_parts) == 0

        result = {
            "tenant_id": tenant_id,
            "incident_id": incident_id,
            "required_parts": results,
            "all_parts_available": all_available,
            "missing_parts": missing_parts,
            "status": (
                "available"
                if all_available
                else "insufficient_stock"
            )
        }

        logger.info(
            "Consulta de repuestos para incidente %s: %s",
            incident_id,
            result["status"]
        )

        return result

    except ClientError:
        logger.exception(
            "Error de DynamoDB buscando repuestos"
        )
        raise

    except Exception:
        logger.exception("Error buscando repuestos")
        raise
