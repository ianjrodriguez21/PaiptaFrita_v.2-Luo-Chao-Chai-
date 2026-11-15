
"""Lambda CrearIncidente: registra incidentes evaluados en DynamoDB.

Contrato de tabla provisional (verificar al integrar la infraestructura):
  PK: tenant_id (String)
  SK: sk (String), con valor 'INC#<incident_id>'

La clave de idempotencia es tenant_id + machine_id + timestamp de detección.
Para solicitudes sin timestamp se genera un identificador nuevo.
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

INCIDENTS_TABLE_NAME = os.environ.get("INCIDENTS_TABLE_NAME", "t_incidentes")
_table = boto3.resource("dynamodb").Table(INCIDENTS_TABLE_NAME)

VALID_SEVERITIES = {"low", "medium", "high", "critical"}


def _payload(event):
    if not isinstance(event, dict):
        raise ValueError("El evento debe ser un objeto JSON")

    body = event.get("body")

    if isinstance(body, str):
        return json.loads(body)

    if isinstance(body, dict):
        return body

    return event


def _unwrap(value):
    """Admite salidas Lambda directas o envolturas de Step Functions."""
    if isinstance(value, dict) and isinstance(value.get("Payload"), dict):
        return value["Payload"]

    return value if isinstance(value, dict) else {}


def lambda_handler(event, context):
    data = _payload(event)
    evaluation = _unwrap(data.get("evaluation_result")) or data

    tenant_id = data.get("tenant_id") or evaluation.get("tenant_id")
    machine_id = data.get("machine_id") or evaluation.get("machine_id")

    incident_required = evaluation.get(
        "incident_required", data.get("incident_required")
    )
    risk_score = evaluation.get("risk_score", data.get("risk_score"))
    incident_type = evaluation.get("incident_type", data.get("incident_type"))
    severity = evaluation.get("severity", data.get("severity"))

    # Validaciones
    if not tenant_id or not machine_id:
        raise ValueError("tenant_id y machine_id son obligatorios")

    if incident_required is not True:
        raise ValueError(
            "CrearIncidente solo recibe evaluaciones con incident_required=true"
        )

    if not isinstance(incident_type, str) or not incident_type.strip():
        raise ValueError("incident_type es obligatorio")

    if severity not in VALID_SEVERITIES:
        raise ValueError(
            "severity debe ser low, medium, high o critical"
        )

    if isinstance(risk_score, bool) or risk_score is None:
        raise ValueError(
            "risk_score debe ser un número entre 0 y 100"
        )

    try:
        risk_score_num = float(risk_score)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("risk_score debe ser numérico") from exc

    if not 0 <= risk_score_num <= 100:
        raise ValueError("risk_score fuera del rango 0-100")

    # Obtener fecha de detección
    anomaly = _unwrap(data.get("anomaly_result"))

    detected_at = (
        data.get("detected_at")
        or anomaly.get("timestamp")
        or data.get("timestamp")
        or datetime.now(timezone.utc).isoformat()
    )

    # Generar identificador determinístico
    # para evitar duplicados durante reintentos
    event_id = data.get("event_id")

    fingerprint = "|".join(
        map(str, [tenant_id, machine_id, event_id or detected_at])
    )

    incident_id = (
        "INC-"
        + hashlib.sha256(fingerprint.encode("utf-8"))
        .hexdigest()[:16]
        .upper()
    )

    # Construir registro DynamoDB
    item = {
        "tenant_id": tenant_id,
        "sk": f"INC#{incident_id}",
        "incident_id": incident_id,
        "machine_id": machine_id,
        "detected_at": detected_at,
        "incident_type": incident_type,
        "severity": severity,
        "risk_score": (
            int(risk_score_num)
            if risk_score_num.is_integer()
            else Decimal(str(risk_score_num))
        ),
        "status": "open",
    }

    try:
        _table.put_item(
            Item=item,
            ConditionExpression=(
                "attribute_not_exists(tenant_id) "
                "AND attribute_not_exists(sk)"
            ),
        )

    except ClientError as exc:
        if (
            exc.response.get("Error", {}).get("Code")
            != "ConditionalCheckFailedException"
        ):
            logger.exception(
                "No se pudo guardar incidente en DynamoDB"
            )
            raise

        # Si el incidente ya existe, devolver su estado
        existing = _table.get_item(
            Key={
                "tenant_id": tenant_id,
                "sk": item["sk"],
            },
            ConsistentRead=True,
        ).get("Item")

        if existing is None:
            raise RuntimeError(
                "El registro duplicado no pudo consultarse"
            ) from exc

        return {
            key: existing[key]
            for key in (
                "tenant_id",
                "incident_id",
                "machine_id",
                "detected_at",
                "incident_type",
                "severity",
                "risk_score",
                "status",
            )
            if key in existing
        }

    logger.info("Incidente creado: %s", incident_id)

    return {
        key: item[key]
        for key in (
            "tenant_id",
            "incident_id",
            "machine_id",
            "detected_at",
            "incident_type",
            "severity",
            "risk_score",
            "status",
        )
    }
