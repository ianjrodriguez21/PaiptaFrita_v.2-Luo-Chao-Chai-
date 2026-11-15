
"""
Lambda: NotificarUsuario

Servicio: Amazon SNS

Entrada:
    tenant_id
    incident_result
    notification_request

Salida:
    notification_result

No utiliza IA.
No modifica incidentes.

El destinatario debe estar previamente validado.
"""

import os
import json
import hashlib
import logging
from datetime import datetime, timezone

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

sns = boto3.client("sns")

SNS_TOPIC_ARN = os.environ["SNS_TOPIC_ARN"]

VALID_TYPES = {
    "incident_created",
    "incident_assignment",
    "critical_incident",
    "maintenance_scheduled",
    "incident_resolved",
    "low_stock"
}

VALID_ROLES = {
    "operator",
    "technician",
    "supervisor",
    "manager",
    "admin"
}


def build_message(notification_type, incident_id):
    messages = {
        "incident_created":
            f"Incident {incident_id} has been created.",

        "incident_assignment":
            f"Incident {incident_id} has been assigned.",

        "critical_incident":
            f"Critical incident {incident_id} requires attention.",

        "maintenance_scheduled":
            f"Maintenance scheduled for incident {incident_id}.",

        "incident_resolved":
            f"Incident {incident_id} has been resolved.",

        "low_stock":
            f"Insufficient stock for incident {incident_id}."
    }

    return messages[notification_type]


def lambda_handler(event, context):
    try:
        # 1. Recuperar contexto
        tenant_id = event.get("tenant_id")
        incident = event.get("incident_result")
        request = event.get("notification_request")

        if not tenant_id:
            raise ValueError("tenant_id obligatorio")

        if not isinstance(incident, dict):
            raise ValueError(
                "Falta incident_result"
            )

        if not isinstance(request, dict):
            raise ValueError(
                "Falta notification_request"
            )

        incident_id = incident.get("incident_id")
        user_id = request.get("user_id")
        notification_type = request.get(
            "notification_type"
        )
        role = request.get("role")

        if not incident_id or not user_id:
            raise ValueError(
                "incident_id y user_id obligatorios"
            )

        if incident.get("tenant_id") != tenant_id:
            raise ValueError(
                "El incidente pertenece a otro tenant"
            )

        if notification_type not in VALID_TYPES:
            raise ValueError(
                "notification_type invalido"
            )

        if role not in VALID_ROLES:
            raise ValueError(
                "role invalido"
            )

        # 2. Comprobar condiciones del evento
        if notification_type == "incident_resolved":
            closure = event.get("closure_result")

            if (
                not isinstance(closure, dict)
                or closure.get("status") != "resolved"
                or closure.get("incident_id") != incident_id
                or closure.get("tenant_id") != tenant_id
            ):
                raise ValueError(
                    "No existe un cierre valido"
                )

        if notification_type == "incident_assignment":
            assignment = event.get("assignment_result")

            if (
                not isinstance(assignment, dict)
                or assignment.get("assignment_status") != "assigned"
                or assignment.get("incident_id") != incident_id
                or assignment.get("tenant_id") != tenant_id
            ):
                raise ValueError(
                    "No existe una asignacion valida"
                )

        if notification_type == "critical_incident":
            if incident.get("severity") != "critical":
                raise ValueError(
                    "El incidente no es critico"
                )

        if notification_type == "low_stock":
            parts = event.get("parts_result")

            if (
                not isinstance(parts, dict)
                or parts.get("incident_id") != incident_id
                or parts.get("status") != "insufficient_stock"
            ):
                raise ValueError(
                    "No existe una alerta de stock valida"
                )

        # 3. Construir mensaje
        message = request.get("message")

        if message is None:
            message = build_message(
                notification_type,
                incident_id
            )

        if (
            not isinstance(message, str)
            or not message.strip()
        ):
            raise ValueError("Mensaje invalido")

        # 4. Identificador estable de notificacion
        fingerprint = "|".join([
            tenant_id,
            incident_id,
            user_id,
            notification_type
        ])

        notification_id = (
            "NOT-"
            + hashlib.sha256(
                fingerprint.encode("utf-8")
            ).hexdigest()[:16].upper()
        )

        published_at = datetime.now(
            timezone.utc
        ).isoformat()

        notification = {
            "tenant_id": tenant_id,
            "notification_id": notification_id,
            "user_id": user_id,
            "role": role,
            "incident_id": incident_id,
            "notification_type": notification_type,
            "message": message,
            "published_at": published_at
        }

        # 5. Atributos para filtros SNS
        attributes = {
            "tenant_id": {
                "DataType": "String",
                "StringValue": tenant_id
            },
            "user_id": {
                "DataType": "String",
                "StringValue": user_id
            },
            "role": {
                "DataType": "String",
                "StringValue": role
            },
            "notification_type": {
                "DataType": "String",
                "StringValue": notification_type
            }
        }

        # 6. Publicar evento SNS
        response = sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject="ArquiFusionCorp Notification",
            Message=json.dumps(
                notification,
                ensure_ascii=False
            ),
            MessageAttributes=attributes
        )

        logger.info(
            "Notificacion %s publicada",
            notification_id
        )

        # 7. JSON directo para Step Functions
        return {
            **notification,
            "sns_message_id": response["MessageId"],
            "status": "published"
        }

    except Exception:
        logger.exception(
            "Error en NotificarUsuario"
        )
        raise
