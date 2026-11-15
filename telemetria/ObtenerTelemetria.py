
import json
import os
import uuid
import logging
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

S3_BUCKET_NAME = os.environ.get(
    "S3_BUCKET_NAME",
    "bucket-telemetria-historico"
)

DYNAMODB_TABLE_NAME = os.environ.get(
    "DYNAMODB_TABLE_NAME",
    "t_maquina"
)

table = dynamodb.Table(DYNAMODB_TABLE_NAME)


def normalize_timestamp(value):
    """Convierte fechas ISO 8601 a UTC normalizado."""

    if value is None:
        date = datetime.now(timezone.utc)
    else:
        if not isinstance(value, str):
            raise ValueError(
                "timestamp debe ser una fecha ISO 8601"
            )

        try:
            date = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError(
                "Formato de timestamp inválido"
            ) from exc

        if date.tzinfo is None:
            raise ValueError(
                "timestamp debe incluir zona horaria"
            )

        date = date.astimezone(timezone.utc)

    return date.isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def response(status_code, body):
    return {
        "statusCode": status_code,
        "body": json.dumps(body)
    }


def lambda_handler(event, context):
    try:
        # 1. Obtener datos
        if isinstance(event.get("body"), str):
            telemetry = json.loads(event["body"])
        elif isinstance(event.get("body"), dict):
            telemetry = event["body"]
        else:
            telemetry = event

        if not isinstance(telemetry, dict):
            raise ValueError(
                "La telemetría debe ser un objeto JSON"
            )

        telemetry = telemetry.copy()

        tenant_id = telemetry.get("tenant_id")
        machine_id = telemetry.get("machine_id")

        if not tenant_id or not machine_id:
            return response(400, {
                "error": (
                    "tenant_id y machine_id son obligatorios"
                )
            })

        # 2. Mantener el timestamp en la máquina
        timestamp = normalize_timestamp(
            telemetry.get("timestamp")
        )

        telemetry["timestamp"] = timestamp

        # 3. Guardar histórico en S3
        # Un archivo por lectura, organizado por máquina
        unique_id = uuid.uuid4().hex[:12]

        s3_key = (
            f"{tenant_id}/"
            f"{machine_id}/"
            f"{timestamp}_{unique_id}.json"
        )

        s3.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=s3_key,
            Body=json.dumps(telemetry).encode("utf-8"),
            ContentType="application/json"
        )

        # 4. Preparar registro actual DynamoDB
        # Las claves serán tenant_id y machine_id
        item = json.loads(
            json.dumps(telemetry),
            parse_float=Decimal
        )

        # No usar sk = machine_id#timestamp
        # Cada máquina ocupa un único registro
        item.pop("sk", None)
        item.pop("pk", None)
        item.pop("machine_timestamp", None)

        item["tenant_id"] = tenant_id
        item["machine_id"] = machine_id
        item["timestamp"] = timestamp

        # 5. Guardar solamente si es una lectura
        # más reciente que la almacenada
        updated_current = False

        try:
            table.put_item(
                Item=item,
                ConditionExpression=(
                    "attribute_not_exists(#ts) "
                    "OR #ts <= :incoming"
                ),
                ExpressionAttributeNames={
                    "#ts": "timestamp"
                },
                ExpressionAttributeValues={
                    ":incoming": timestamp
                }
            )

            updated_current = True

        except ClientError as exc:
            code = exc.response.get(
                "Error", {}
            ).get("Code")

            if code != "ConditionalCheckFailedException":
                raise

            # Lectura antigua: queda registrada en S3
            # pero no reemplaza el estado actual
            logger.info(
                "Lectura histórica recibida para %s",
                machine_id
            )

        logger.info(
            "Telemetría procesada: %s / %s",
            tenant_id,
            machine_id
        )

        return response(200, {
            "message": "Telemetría procesada",
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "timestamp": timestamp,
            "stored": {
                "s3": True,
                "dynamodb": updated_current
            },
            "s3_path": s3_key,
            "dynamodb_pk": tenant_id,
            "dynamodb_sk": machine_id,
            "status": "processed"
        })

    except (ValueError, TypeError) as exc:
        logger.warning("Telemetría inválida: %s", exc)
        return response(400, {
            "error": str(exc)
        })

    except Exception:
        logger.exception(
            "Error procesando telemetría"
        )
        return response(500, {
            "error": "Error interno al procesar telemetría"
        })
