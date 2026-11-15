
"""
Lambda: ReservarRepuestos

Tablas provisionales:

t_repuestos:
    PK: tenant_id
    SK: part_id

t_reservas_repuestos:
    PK: tenant_id
    SK: sk

La reserva descuenta stock disponible.
Utiliza transacciones para evitar inconsistencias.
"""

import os
import logging
import hashlib
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

PARTS_TABLE_NAME = os.environ.get(
    "PARTS_TABLE_NAME",
    "t_repuestos"
)

RESERVATIONS_TABLE_NAME = os.environ.get(
    "RESERVATIONS_TABLE_NAME",
    "t_reservas_repuestos"
)

parts_table = dynamodb.Table(PARTS_TABLE_NAME)
reservations_table = dynamodb.Table(
    RESERVATIONS_TABLE_NAME
)


def serialize_item(item):
    return {
        key: serializer.serialize(value)
        for key, value in item.items()
    }


def validate_quantity(value):
    if type(value) is not int or value <= 0:
        raise ValueError(
            "quantity debe ser un entero positivo"
        )
    return value


def get_stock_status(quantity, minimum_stock):
    if quantity <= 0:
        return "out_of_stock"

    if quantity <= minimum_stock:
        return "low_stock"

    return "available"


def format_reservation(item, remaining_stock=None):
    result = {
        "tenant_id": item["tenant_id"],
        "reservation_id": item["reservation_id"],
        "incident_id": item["incident_id"],
        "part_id": item["part_id"],
        "quantity": int(item["quantity"]),
        "reserved_at": item["reserved_at"],
        "status": item["status"]
    }

    if remaining_stock is not None:
        result["remaining_stock"] = remaining_stock

    return result


def lambda_handler(event, context):
    try:
        data = event.get("Payload", event)

        tenant_id = data.get("tenant_id")
        incident_id = data.get("incident_id")
        part_id = data.get("part_id")
        quantity = validate_quantity(
            data.get("quantity")
        )

        if not all([tenant_id, incident_id, part_id]):
            raise ValueError(
                "tenant_id, incident_id y part_id "
                "son obligatorios"
            )

        # Identificador estable por incidente y repuesto
        fingerprint = (
            f"{tenant_id}|{incident_id}|{part_id}"
        )

        reservation_id = (
            "RES-"
            + hashlib.sha256(
                fingerprint.encode("utf-8")
            ).hexdigest()[:16].upper()
        )

        reservation_key = {
            "tenant_id": tenant_id,
            "sk": f"RES#{incident_id}#{part_id}"
        }

        # Comprobar una reserva anterior
        existing = reservations_table.get_item(
            Key=reservation_key,
            ConsistentRead=True
        ).get("Item")

        if existing:
            if int(existing["quantity"]) != quantity:
                raise ValueError(
                    "Ya existe una reserva con otra cantidad"
                )
            return format_reservation(existing)

        # Reintentar ante actualizaciones concurrentes
        for attempt in range(3):

            stock_response = parts_table.get_item(
                Key={
                    "tenant_id": tenant_id,
                    "part_id": part_id
                },
                ConsistentRead=True
            )

            part = stock_response.get("Item")

            if not part:
                return {
                    "tenant_id": tenant_id,
                    "incident_id": incident_id,
                    "part_id": part_id,
                    "status": "part_not_found",
                    "reserved": False
                }

            current_stock = int(
                part.get("quantity", 0)
            )

            minimum_stock = int(
                part.get("minimum_stock", 0)
            )

            # Sin stock suficiente
            if current_stock < quantity:
                return {
                    "tenant_id": tenant_id,
                    "incident_id": incident_id,
                    "part_id": part_id,
                    "required_quantity": quantity,
                    "available_quantity": current_stock,
                    "status": "insufficient_stock",
                    "reserved": False
                }

            remaining_stock = current_stock - quantity

            new_status = get_stock_status(
                remaining_stock,
                minimum_stock
            )

            reserved_at = datetime.now(
                timezone.utc
            ).isoformat()

            reservation_item = {
                **reservation_key,
                "reservation_id": reservation_id,
                "incident_id": incident_id,
                "part_id": part_id,
                "quantity": quantity,
                "reserved_at": reserved_at,
                "status": "reserved"
            }

            try:
                # Descontar stock y crear reserva
                # como una única operación atómica.
                client.transact_write_items(
                    TransactItems=[
                        {
                            "Update": {
                                "TableName": PARTS_TABLE_NAME,
                                "Key": serialize_item({
                                    "tenant_id": tenant_id,
                                    "part_id": part_id
                                }),
                                "UpdateExpression": (
                                    "SET #qty = :remaining, "
                                    "#status = :new_status"
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
                                "ExpressionAttributeValues": (
                                    serialize_item({
                                        ":remaining": remaining_stock,
                                        ":new_status": new_status,
                                        ":original": current_stock
                                    })
                                )
                            }
                        },
                        {
                            "Put": {
                                "TableName": (
                                    RESERVATIONS_TABLE_NAME
                                ),
                                "Item": serialize_item(
                                    reservation_item
                                ),
                                "ConditionExpression": (
                                    "attribute_not_exists(tenant_id) "
                                    "AND attribute_not_exists(sk)"
                                )
                            }
                        }
                    ]
                )

                logger.info(
                    "Reserva %s creada correctamente",
                    reservation_id
                )

                return {
                    **format_reservation(
                        reservation_item,
                        remaining_stock
                    ),
                    "reserved": True,
                    "stock_status": new_status
                }

            except ClientError as exc:
                code = exc.response.get(
                    "Error", {}
                ).get("Code")

                if code != "TransactionCanceledException":
                    raise

                # Si otro intento ya creó la reserva,
                # devolver esa misma reserva.
                existing = reservations_table.get_item(
                    Key=reservation_key,
                    ConsistentRead=True
                ).get("Item")

                if existing:
                    if int(existing["quantity"]) != quantity:
                        raise ValueError(
                            "Reserva existente con otra cantidad"
                        )
                    return format_reservation(existing)

                reasons = exc.response.get(
                    "CancellationReasons", []
                )

                # Solo reintentar ante conflicto de stock.
                if reasons and reasons[0].get(
                    "Code"
                ) == "ConditionalCheckFailed":
                    continue

                raise

        raise RuntimeError(
            "No se pudo reservar por cambios concurrentes "
            "en el inventario"
        )

    except Exception:
        logger.exception(
            "Error reservando repuestos"
        )
        raise
