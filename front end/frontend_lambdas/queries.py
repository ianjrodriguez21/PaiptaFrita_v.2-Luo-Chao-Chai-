"""Consultas del dashboard para API Gateway HTTP API (payload 2.0)."""

import json
import logging
import os
from decimal import Decimal
from functools import lru_cache

LOG = logging.getLogger(__name__)

TABLES = {
    "machines": "MACHINES_TABLE_NAME",
    "incidents": "INCIDENTS_TABLE_NAME",
    "maintenance": "MAINTENANCE_TABLE_NAME",
    "technicians": "TECHNICIANS_TABLE_NAME",
    "parts": "PARTS_TABLE_NAME",
    "utilization": "UTILIZATION_TABLE_NAME",
    "efficacy": "EFFICACY_TABLE_NAME",
    "predictions": "PREDICTIONS_TABLE_NAME",
}
DASHBOARD = ("machines", "incidents", "maintenance", "utilization", "efficacy", "predictions")
TECHNICIAN_FIELDS = ("technician_id", "availability", "specialties")


class RequestError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


@lru_cache(maxsize=1)
def dynamodb():
    import boto3

    return boto3.resource("dynamodb")


def table(resource):
    name = os.getenv(TABLES[resource])
    if not name:
        raise RequestError(503, "Fuente de datos pendiente de configurar")
    return dynamodb().Table(name)


def tenant_from_event(event):
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    if not claims.get("sub"):
        raise RequestError(401, "Se requiere autenticación")
    tenant = claims.get("custom:tenant_id")
    if not isinstance(tenant, str) or not tenant.strip():
        raise RequestError(403, "La sesión no tiene una organización asignada")
    return tenant


def json_value(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"Tipo JSON no admitido: {type(value).__name__}")


def response(status, payload):
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(payload, default=json_value, ensure_ascii=False),
    }


def query_page(resource, tenant, parameters):
    try:
        limit = int(parameters.get("limit", "25"))
    except (TypeError, ValueError):
        raise RequestError(400, "limit debe ser un entero entre 1 y 100") from None
    if not 1 <= limit <= 100:
        raise RequestError(400, "limit debe ser un entero entre 1 y 100")

    kwargs = {
        "KeyConditionExpression": "#tenant = :tenant",
        "ExpressionAttributeNames": {"#tenant": "tenant_id"},
        "ExpressionAttributeValues": {":tenant": tenant},
        "Limit": limit,
    }
    cursor = parameters.get("cursor")
    if cursor is not None:
        if not isinstance(cursor, str) or not cursor or len(cursor.encode("utf-8")) > 1024:
            raise RequestError(400, "Cursor inválido")
        sort_key = {
            "machines": "machine_id", "incidents": "sk", "technicians": "technician_id",
            "parts": "part_id", "utilization": "machine_id", "efficacy": "fecha",
            "predictions": "sk", "maintenance": os.getenv("MAINTENANCE_SORT_KEY"),
        }[resource]
        if not sort_key:
            raise RequestError(503, "Clave de mantenimiento pendiente de configurar")
        kwargs["ExclusiveStartKey"] = {"tenant_id": tenant, sort_key: cursor}

    result = table(resource).query(**kwargs)
    items = result["Items"]
    if resource == "technicians":
        items = [{field: row[field] for field in TECHNICIAN_FIELDS if field in row} for row in items]
    last_key = result.get("LastEvaluatedKey")
    next_cursor = next(value for key, value in last_key.items() if key != "tenant_id") if last_key else None
    return {"items": items, "next_cursor": next_cursor}


def machine_detail(tenant, event):
    machine = (event.get("pathParameters") or {}).get("machine_id")
    if not machine:
        raise RequestError(400, "Falta machine_id")
    item = table("machines").get_item(Key={"tenant_id": tenant, "machine_id": machine}).get("Item")
    if item is None:
        raise RequestError(404, "Máquina no encontrada")
    return item


def dashboard(tenant):
    sections = {}
    for resource in DASHBOARD:
        if not os.getenv(TABLES[resource]):
            sections[resource] = {"status": "not_configured"}
            continue
        sections[resource] = {"status": "available", **query_page(resource, tenant, {})}
    return {"sections": sections}


def handle(event, resource):
    try:
        tenant = tenant_from_event(event)
        if resource == "machine_detail":
            return response(200, machine_detail(tenant, event))
        if resource == "dashboard":
            return response(200, dashboard(tenant))
        return response(200, query_page(resource, tenant, event.get("queryStringParameters") or {}))
    except RequestError as error:
        return response(error.status, {"message": str(error)})
    except Exception:
        LOG.error("Falló la consulta del dashboard a %s", resource)
        return response(500, {"message": "No se pudo completar la consulta"})
