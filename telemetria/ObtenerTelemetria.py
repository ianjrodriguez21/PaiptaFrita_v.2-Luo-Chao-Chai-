
"""
ObtenerTelemetria.py

API Gateway -> Lambda -> S3 + DynamoDB
                            |
                            v
                     Step Functions

Tabla t_maquina:
PK: tenant_id
SK: machine_id
timestamp: atributo obligatorio
"""

import os
import re
import json
import hashlib
import logging
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
stepfunctions = boto3.client("stepfunctions")

S3_BUCKET_NAME = os.environ["S3_BUCKET_NAME"]

MACHINES_TABLE_NAME = os.environ.get(
    "MACHINES_TABLE_NAME", "t_machines"
)

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]

table = dynamodb.Table(MACHINES_TABLE_NAME)


def http_response(code, data):
    return {
        "statusCode": code,
        "headers": {
            "Content-Type": "application/json"
        },
        "body": json.dumps(data)
    }


def normalize_timestamp(value):
    if value is None:
        dt = datetime.now(timezone.utc)
    else:
        dt = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            raise ValueError(
                "timestamp debe incluir zona horaria"
            )

        dt = dt.astimezone(timezone.utc)

    return dt.isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def validate_id(value, field):
    if (
        not isinstance(value, str)
        or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,80}", value
        )
    ):
        raise ValueError(
            f"{field} tiene un formato inválido"
        )

    return value


def lambda_handler(event, context):
    try:
        # 1. Recibir telemetría
        body = event.get("body", event)

        if isinstance(body, str):
            telemetry = json.loads(body)
        else:
            telemetry = body

        if not isinstance(telemetry, dict):
            raise ValueError(
                "La telemetría debe ser un objeto JSON"
            )

        telemetry = telemetry.copy()

        tenant_id = validate_id(
            telemetry.get("tenant_id"),
            "tenant_id"
        )

        machine_id = validate_id(
            telemetry.get("machine_id"),
            "machine_id"
        )

        timestamp = normalize_timestamp(
            telemetry.get("timestamp")
        )

        telemetry["timestamp"] = timestamp

        # El tenant debe validarse contra la identidad
        # autenticada por API Gateway en producción.

        # 2. Generar identificador determinístico
        canonical = json.dumps(
            telemetry,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False
        )

        event_id = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()

        # 3. Guardar histórico en S3
        s3_key = (
            f"{tenant_id}/"
            f"{machine_id}/"
            f"{timestamp}_{event_id[:16]}.json"
        )

        s3.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=s3_key,
            Body=canonical.encode("utf-8"),
            ContentType="application/json"
        )

        # 4. Guardar estado actual en t_maquina
        item = json.loads(
            canonical,
            parse_float=Decimal
        )

        item.pop("sk", None)
        item.pop("pk", None)

        item["tenant_id"] = tenant_id
        item["machine_id"] = machine_id

        updated = False
        outdated = False

        try:
            table.put_item(
                Item=item,
                ConditionExpression=(
                    "attribute_not_exists(#ts) "
                    "OR #ts < :incoming"
                ),
                ExpressionAttributeNames={
                    "#ts": "timestamp"
                },
                ExpressionAttributeValues={
                    ":incoming": timestamp
                }
            )
            updated = True

        except ClientError as exc:
            if exc.response["Error"]["Code"] != (
                "ConditionalCheckFailedException"
            ):
                raise

            # Verificar si es un reintento o lectura antigua
            previous = table.get_item(
                Key={
                    "tenant_id": tenant_id,
                    "machine_id": machine_id
                },
                ConsistentRead=True
            ).get("Item", {})

            previous_timestamp = previous.get(
                "timestamp", ""
            )

            outdated = previous_timestamp > timestamp

        # No evaluar lecturas anteriores
        if outdated:
            return http_response(200, {
                "status": "historical_only",
                "tenant_id": tenant_id,
                "machine_id": machine_id,
                "timestamp": timestamp,
                "stored_in_s3": True,
                "state_machine_started": False
            })

        # 5. Contexto inicial de Step Functions
        workflow_input = {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "timestamp": timestamp,
            "event_id": event_id,
            "machine_data": telemetry,
            "storage": {
                "bucket": S3_BUCKET_NAME,
                "key": s3_key,
                "current_state_updated": updated
            }
        }

        execution_name = (
            "telemetry-" + event_id[:40]
        )

        # 6. Iniciar evaluación de mantenimiento
        try:
            execution = stepfunctions.start_execution(
                stateMachineArn=STATE_MACHINE_ARN,
                name=execution_name,
                input=json.dumps(workflow_input)
            )

            execution_arn = execution["executionArn"]

        except ClientError as exc:
            if (
                exc.response["Error"]["Code"]
                != "ExecutionAlreadyExists"
            ):
                raise

            # Reintento de un evento ya ejecutado
            execution_arn = None

            logger.info(
                "Ejecución previa para evento %s",
                event_id
            )

        return http_response(202, {
            "status": "accepted",
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "timestamp": timestamp,
            "event_id": event_id,
            "dynamodb_updated": updated,
            "s3_path": s3_key,
            "execution_name": execution_name,
            "execution_arn": execution_arn
        })

    except (ValueError, TypeError) as exc:
        return http_response(400, {
            "error": str(exc)
        })

    except Exception:
        logger.exception(
            "Error procesando telemetría"
        )

        return http_response(500, {
            "error": "No se pudo procesar la telemetría"
        })
