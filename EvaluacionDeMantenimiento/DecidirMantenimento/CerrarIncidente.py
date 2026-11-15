
"""
Lambda: CerrarIncidente

Tabla: t_incidentes
PK: tenant_id
SK: sk = INC#incident_id

Entrada:
    incident_result
    intervention_result

Salida:
    closure_result

Solo cierra intervenciones completadas
con resultado operational.
"""

import os
import logging
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")

INCIDENTS_TABLE_NAME = os.environ.get(
    "INCIDENTS_TABLE_NAME",
    "t_incidents"
)

table = dynamodb.Table(INCIDENTS_TABLE_NAME)


def format_result(item, closed_now):
    return {
        "tenant_id": item["tenant_id"],
        "incident_id": item["incident_id"],
        "machine_id": item["machine_id"],
        "intervention_id": item.get("intervention_id"),
        "status": item["status"],
        "resolved_at": item.get("resolved_at"),
        "resolution": item.get("resolution"),
        "closed_now": closed_now
    }


def lambda_handler(event, context):
    try:
        tenant_id = event.get("tenant_id")
        machine_id = event.get("machine_id")

        incident = event.get("incident_result")
        intervention = event.get("intervention_result")

        if not tenant_id or not machine_id:
            raise ValueError(
                "tenant_id y machine_id obligatorios"
            )

        if not isinstance(incident, dict):
            raise ValueError(
                "Falta incident_result"
            )

        if not isinstance(intervention, dict):
            raise ValueError(
                "Falta intervention_result"
            )

        incident_id = incident.get("incident_id")
        intervention_id = intervention.get(
            "intervention_id"
        )

        if not incident_id or not intervention_id:
            raise ValueError(
                "IDs de incidente e intervencion obligatorios"
            )

        # 1. Comprobar contexto
        if (
            incident.get("tenant_id") != tenant_id
            or incident.get("machine_id") != machine_id
            or intervention.get("tenant_id") != tenant_id
            or intervention.get("machine_id") != machine_id
            or intervention.get("incident_id") != incident_id
        ):
            raise ValueError(
                "Incidente e intervencion no coinciden"
            )

        # 2. Verificar reparación completada
        if intervention.get("status") != "completed":
            raise ValueError(
                "La intervencion no ha terminado"
            )

        if intervention.get("result") != "operational":
            raise ValueError(
                "No se puede cerrar: equipo no operativo"
            )

        diagnosis = intervention.get("diagnosis")

        if not isinstance(diagnosis, str) or not diagnosis.strip():
            raise ValueError(
                "La intervencion necesita diagnostico"
            )

        resolution = event.get("resolution")

        if resolution is None:
            resolution = diagnosis

        if not isinstance(resolution, str) or not resolution.strip():
            raise ValueError(
                "resolution obligatoria"
            )

        resolved_at = datetime.now(
            timezone.utc
        ).isoformat()

        key = {
            "tenant_id": tenant_id,
            "sk": f"INC#{incident_id}"
        }

        # 3. Cerrar solo desde in_progress
        try:
            response = table.update_item(
                Key=key,
                UpdateExpression=(
                    "SET #status = :resolved, "
                    "resolved_at = :resolved_at, "
                    "resolution = :resolution, "
                    "intervention_id = :intervention"
                ),
                ConditionExpression=(
                    "attribute_exists(tenant_id) "
                    "AND attribute_exists(sk) "
                    "AND machine_id = :machine "
                    "AND #status = :progress"
                ),
                ExpressionAttributeNames={
                    "#status": "status"
                },
                ExpressionAttributeValues={
                    ":resolved": "resolved",
                    ":resolved_at": resolved_at,
                    ":resolution": resolution.strip(),
                    ":intervention": intervention_id,
                    ":machine": machine_id,
                    ":progress": "in_progress"
                },
                ReturnValues="ALL_NEW"
            )

            return format_result(
                response["Attributes"],
                closed_now=True
            )

        except ClientError as exc:
            if exc.response["Error"]["Code"] != (
                "ConditionalCheckFailedException"
            ):
                raise

            existing = table.get_item(
                Key=key,
                ConsistentRead=True
            ).get("Item")

            if not existing:
                raise ValueError(
                    "El incidente no existe"
                )

            if existing.get("machine_id") != machine_id:
                raise ValueError(
                    "El incidente pertenece a otra maquina"
                )

            # Reintento de un cierre anterior
            if existing.get("status") == "resolved":
                if existing.get(
                    "intervention_id"
                ) != intervention_id:
                    raise ValueError(
                        "Incidente cerrado por otra intervencion"
                    )

                return format_result(
                    existing,
                    closed_now=False
                )

            raise ValueError(
                "Estado de incidente no permite cierre"
            )

    except Exception:
        logger.exception(
            "Error en CerrarIncidente"
        )
        raise
