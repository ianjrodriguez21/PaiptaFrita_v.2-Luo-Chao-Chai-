"""Registra el perfil de una cuenta previamente aprovisionada en Cognito."""

import json
import logging
import os
from decimal import Decimal, InvalidOperation
from uuid import UUID

from .queries import RequestError, dynamodb, response, tenant_from_event

LOG = logging.getLogger(__name__)
ROLES = {
    "operator": "Operador", "technician": "Técnico", "supervisor": "Supervisor",
    "manager": "Gerente", "admin": "Administrador",
}
AVAILABILITIES = {"available": "Disponible", "unavailable": "No disponible"}


def coordinate(value, maximum):
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise RequestError(400, "Ingresa latitud y longitud válidas") from None
    if not number.is_finite() or abs(number) > maximum:
        raise RequestError(400, "Latitud o longitud fuera de rango")
    return number


def profiles(tenant, payload):
    if not isinstance(payload, dict):
        raise RequestError(400, "El cuerpo debe ser un objeto JSON")
    try:
        user_id = str(UUID(payload.get("user_id", "")))
    except (ValueError, TypeError, AttributeError):
        raise RequestError(400, "Ingresa el sub de Cognito") from None
    name, email, role = (payload.get(key) for key in ("name", "email", "role"))
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
        raise RequestError(400, "Ingresa el nombre")
    if not isinstance(email, str) or len(email) > 254 or email.count("@") != 1 or any(
        not part or any(character.isspace() for character in part) for part in email.split("@")
    ):
        raise RequestError(400, "Ingresa un correo válido")
    if not isinstance(role, str) or role not in ROLES:
        raise RequestError(400, "Selecciona un rol válido")
    profile = {
        "tenant_id": tenant, "user_id": user_id, "name": name.strip(),
        "email": email, "role": role, "status": "active",
    }
    if role != "technician":
        return profile, None
    specialties = payload.get("specialties")
    if not isinstance(specialties, list) or not 1 <= len(specialties) <= 20 or any(
        not isinstance(item, str) or not 1 <= len(item.strip()) <= 80 for item in specialties
    ):
        raise RequestError(400, "Ingresa al menos una especialidad válida")
    availability = payload.get("availability")
    if not isinstance(availability, str) or availability not in AVAILABILITIES:
        raise RequestError(400, "Selecciona la disponibilidad inicial")
    return profile, {
        "tenant_id": tenant, "user_id": user_id, "technician_id": user_id,
        "name": profile["name"], "specialties": list(dict.fromkeys(
            item.strip().lower() for item in specialties
        )),
        "availability": availability, "latitude": coordinate(payload.get("latitude"), 90),
        "longitude": coordinate(payload.get("longitude"), 180),
    }


def register(tenant, actor, profile, technician):
    from botocore.exceptions import ClientError

    users = os.environ.get("USERS_TABLE_NAME")
    technicians = os.environ.get("TECHNICIANS_TABLE_NAME")
    if not users or (technician and not technicians):
        raise RequestError(503, "Falta configurar las tablas de perfiles")
    if actor == profile["user_id"]:
        raise RequestError(409, "Tu perfil administrador ya existe")

    def put(table_name, item):
        return {"Put": {
            "TableName": table_name,
            "Item": item,
            "ConditionExpression": "attribute_not_exists(tenant_id)",
        }}

    actions = [{"ConditionCheck": {
        "TableName": users,
        "Key": {"tenant_id": tenant, "user_id": actor},
        "ConditionExpression": "#role = :admin AND #status = :active",
        "ExpressionAttributeNames": {"#role": "role", "#status": "status"},
        "ExpressionAttributeValues": {":admin": "admin", ":active": "active"},
    }}, put(users, profile)]
    if technician:
        actions.append(put(technicians, technician))
    try:
        dynamodb().meta.client.transact_write_items(TransactItems=actions)
    except ClientError as error:
        reasons = error.response.get("CancellationReasons", [])
        if reasons and reasons[0].get("Code") == "ConditionalCheckFailed":
            raise RequestError(403, "Solo un administrador activo puede registrar perfiles") from None
        if any(reason.get("Code") == "ConditionalCheckFailed" for reason in reasons[1:]):
            raise RequestError(409, "El perfil ya existe") from None
        raise


def lambda_handler(event, context):
    try:
        tenant = tenant_from_event(event)
        actor = event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"]
        if not isinstance(actor, str):
            raise RequestError(401, "Sesión inválida")
        try:
            payload = json.loads(event.get("body") or "", parse_float=Decimal)
        except (ValueError, TypeError):
            raise RequestError(400, "El cuerpo debe ser JSON") from None
        profile, technician = profiles(tenant, payload)
        register(tenant, actor, profile, technician)
        return response(201, {"user": profile, "technician": technician})
    except RequestError as error:
        return response(error.status, {"message": str(error)})
    except Exception:
        LOG.error("Falló el registro de perfil")
        return response(500, {"message": "No se pudo registrar el perfil"})
