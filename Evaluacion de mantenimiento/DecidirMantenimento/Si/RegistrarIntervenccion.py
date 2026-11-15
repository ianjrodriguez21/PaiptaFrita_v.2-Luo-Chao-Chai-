
"""
Lambda: RegistrarIntervenccion

Tabla provisional:
t_intervenciones
    PK: tenant_id
    SK: sk

Registra el diagnóstico y trabajo de mantenimiento.
No cierra el incidente ni modifica inventario.
"""

import os
import json
import uuid
import logging
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")

INTERVENTIONS_TABLE_NAME = os.environ.get(
    "INTERVENTIONS_TABLE_NAME",
    "t_intervenciones"
)

table = dynamodb.Table(INTERVENTIONS_TABLE_NAME)

VALID_RESULTS = {
    "operational",
    "requires_additional_repair",
    "out_of_service"
}


def parse_datetime(value):
    """Valida una fecha ISO 8601 con zona horaria."""

    if not isinstance(value, str):
        raise ValueError("La fecha debe ser un string ISO 8601")

    try:
        date = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError(
            f"Fecha inválida: {value}"
        ) from exc

    if date.tzinfo is None:
        raise ValueError(
            "Las fechas deben incluir zona horaria"
        )

    return date.astimezone(timezone.utc)


def validate_parts(parts):
    """Valida los repuestos realmente utilizados."""

    if not isinstance(parts, list):
        raise ValueError("parts_used debe ser una lista")

    validated = []

    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("Repuesto inválido")

        part_id = part.get("part_id")
        quantity = part.get("quantity")

        if (
            not isinstance(part_id, str)
            or not part_id
            or type(quantity) is not int
            or quantity <= 0
        ):
            raise ValueError(
                "Cada repuesto requiere part_id y "
                "quantity entero positivo"
            )

        validated.append({
            "part_id": part_id,
            "quantity": quantity
        })

    return validated


def lambda_handler(event, context):

    try:
        data = event.get("Payload", event)

        if isinstance(data.get("body"), str):
            data = json.loads(data["body"])

        tenant_id = data.get("tenant_id")
        incident_id = data.get("incident_id")
        machine_id = data.get("machine_id")
        technician_id = data.get("technician_id")

        if not all([
            tenant_id,
            incident_id,
            machine_id,
            technician_id
        ]):
            raise ValueError(
                "tenant_id, incident_id, machine_id "
                "y technician_id son obligatorios"
            )

        started_at = data.get("started_at")

        if not started_at:
            raise ValueError(
                "started_at es obligatorio"
            )

        start_date = parse_datetime(started_at)

        completed_at = data.get("completed_at")
        diagnosis = data.get("diagnosis")
        result = data.get("result")

        parts_used = validate_parts(
            data.get("parts_used", [])
        )

        # Determinar si ya terminó la intervención
        if completed_at is not None:
            end_date = parse_datetime(completed_at)

            if end_date < start_date:
                raise ValueError(
                    "completed_at no puede ser anterior "
                    "a started_at"
                )

            if not diagnosis or result not in VALID_RESULTS:
                raise ValueError(
                    "Una intervención terminada requiere "
                    "diagnosis y result válidos"
                )

            intervention_status = "completed"

            calculated_downtime = round(
                (end_date - start_date).total_seconds() / 60
            )

            downtime_minutes = data.get(
                "downtime_minutes",
                calculated_downtime
            )

            if (
                type(downtime_minutes) is not int
                or downtime_minutes < 0
            ):
                raise ValueError(
                    "downtime_minutes debe ser entero no negativo"
                )

        else:
            intervention_status = "in_progress"
            completed_at = None
            result = None
            downtime_minutes = None

        intervention_id = data.get(
            "intervention_id"
        ) or f"INT-{uuid.uuid4().hex[:12].upper()}"

        # Guardar por identificador de intervención
        item = {
            "tenant_id": tenant_id,
            "sk": f"INT#{intervention_id}",
            "intervention_id": intervention_id,
            "incident_id": incident_id,
            "machine_id": machine_id,
            "technician_id": technician_id,
            "started_at": started_at,
            "completed_at": completed_at,
            "diagnosis": diagnosis,
            "parts_used": parts_used,
            "downtime_minutes": downtime_minutes,
            "result": result,
            "status": intervention_status,
            "created_at": datetime.now(
                timezone.utc
            ).isoformat()
        }

        try:
            table.put_item(
                Item=item,
                ConditionExpression=(
                    "attribute_not_exists(tenant_id) "
                    "AND attribute_not_exists(sk)"
                )
            )

        except ClientError as exc:
            if (
                exc.response.get("Error", {}).get("Code")
                != "ConditionalCheckFailedException"
            ):
                raise

            # Si se repite el identificador, devolver
            # el registro existente sin sobrescribirlo.
            existing = table.get_item(
                Key={
                    "tenant_id": tenant_id,
                    "sk": item["sk"]
                },
                ConsistentRead=True
            ).get("Item")

            if existing is None:
                raise

            item = existing

        logger.info(
            "Intervención %s registrada",
            intervention_id
        )

        return {
            "tenant_id": item["tenant_id"],
            "intervention_id": item["intervention_id"],
            "incident_id": item["incident_id"],
            "machine_id": item["machine_id"],
            "technician_id": item["technician_id"],
            "started_at": item["started_at"],
            "completed_at": item["completed_at"],
            "diagnosis": item["diagnosis"],
            "parts_used": item["parts_used"],
            "downtime_minutes": item["downtime_minutes"],
            "result": item["result"],
            "status": item["status"]
        }

    except Exception:
        logger.exception(
            "Error registrando intervención"
        )
        raise
