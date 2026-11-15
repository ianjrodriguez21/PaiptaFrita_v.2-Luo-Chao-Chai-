
"""
Lambda: NotificarUsuario

Servicio: Amazon SNS

Requiere variable de entorno:
SNS_TOPIC_ARN

Publica notificaciones dirigidas a usuarios.
Las suscripciones SNS deben utilizar filtros
por tenant_id y user_id para evitar entregas
a destinatarios incorrectos.
"""

import os
import json
import logging
import uuid
from datetime import datetime, timezone

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

sns = boto3.client("sns")

SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN")

VALID_NOTIFICATION_TYPES = {
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
    """Construye un mensaje según el evento."""

    messages = {
        "incident_created": (
            f"New incident {incident_id} has been created."
        ),
        "incident_assignment": (
            f"Incident {incident_id} has been assigned."
        ),
        "critical_incident": (
            f"Critical incident {incident_id} requires "
            "immediate attention."
        ),
        "maintenance_scheduled": (
            f"Maintenance scheduled for incident {incident_id}."
        ),
        "incident_resolved": (
            f"Incident {incident_id} has been resolved."
        ),
        "low_stock": (
            f"Insufficient stock detected for incident {incident_id}."
        )
    }

    return messages[notification_type]


def lambda_handler(event, context):
    try:
        data = event.get("Payload", event)

        tenant_id = data.get("tenant_id")
        user_id = data.get("user_id")
        incident_id = data.get("incident_id")
        notification_type = data.get("notification_type")
        role = data.get("role")

        if not all([
            tenant_id,
            user_id,
            incident_id,
            notification_type
        ]):
            raise ValueError(
                "tenant_id, user_id, incident_id y "
                "notification_type son obligatorios"
            )

        if notification_type not in VALID_NOTIFICATION_TYPES:
            raise ValueError(
                "Tipo de notificación no válido"
            )

        if role is not None and role not in VALID_ROLES:
            raise ValueError(
                "Rol de usuario no válido"
            )

        if not SNS_TOPIC_ARN:
            raise ValueError(
                "La variable SNS_TOPIC_ARN no está configurada"
            )

        message = data.get("message")

        if message is None:
            message = build_message(
                notification_type,
                incident_id
            )

        if not isinstance(message, str) or not message.strip():
            raise ValueError(
                "El mensaje debe ser texto no vacío"
            )

        notification_id = data.get(
            "notification_id"
        ) or f"NOT-{uuid.uuid4().hex[:12].upper()}"

        sent_at = datetime.now(
            timezone.utc
        ).isoformat()

        notification = {
            "tenant_id": tenant_id,
            "notification_id": notification_id,
            "user_id": user_id,
            "incident_id": incident_id,
            "notification_type": notification_type,
            "message": message,
            "sent_at": sent_at
        }

        attributes = {
            "tenant_id": {
                "DataType": "String",
                "StringValue": str(tenant_id)
            },
            "user_id": {
                "DataType": "String",
                "StringValue": str(user_id)
            },
            "notification_type": {
                "DataType": "String",
                "StringValue": notification_type
            }
        }

        if role:
            attributes["role"] = {
                "DataType": "String",
                "StringValue": role
            }

        response = sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject="ArquiFusionCorp Notification",
            Message=json.dumps(
                notification,
                ensure_ascii=False
            ),
            MessageAttributes=attributes
        )

        message_id = response["MessageId"]

        logger.info(
            "Notificación %s publicada en SNS: %s",
            notification_id,
            message_id
        )

        return {
            **notification,
            "sns_message_id": message_id,
            "status": "published"
        }

    except Exception:
        logger.exception(
            "Error enviando notificación SNS"
        )
        raise
