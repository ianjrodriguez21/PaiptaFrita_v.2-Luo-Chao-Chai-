
import json
import os
import logging
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")

DYNAMODB_TABLE_NAME = os.environ.get(
    "DYNAMODB_TABLE_NAME",
    "t_utilizations"
)

table = dynamodb.Table(DYNAMODB_TABLE_NAME)


def decimal_minutes(value, name):
    try:
        if isinstance(value, bool):
            raise ValueError()

        number = Decimal(str(value))

        if not number.is_finite() or number < 0:
            raise ValueError()

        return number

    except Exception as exc:
        raise ValueError(
            f"{name} debe ser un número no negativo"
        ) from exc


def lambda_handler(event, context):
    try:
        if isinstance(event.get("body"), str):
            data = json.loads(event["body"])
        elif isinstance(event.get("body"), dict):
            data = event["body"]
        else:
            data = event

        tenant_id = data.get("tenant_id")
        machine_id = data.get("machine_id")
        period = data.get("period")

        if not all([tenant_id, machine_id, period]):
            raise ValueError(
                "tenant_id, machine_id y period "
                "son obligatorios"
            )

        operating = decimal_minutes(
            data.get("operating_minutes", 0),
            "operating_minutes"
        )

        idle = decimal_minutes(
            data.get("idle_minutes", 0),
            "idle_minutes"
        )

        downtime = decimal_minutes(
            data.get("downtime_minutes", 0),
            "downtime_minutes"
        )

        total = operating + idle + downtime

        if total > 0:
            utilization = (
                operating / total * Decimal("100")
            ).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP
            )
        else:
            utilization = Decimal("0.00")

        timestamp = datetime.now(
            timezone.utc
        ).isoformat()

        # PK y SK definitivas
        item = {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "period": period,
            "timestamp": timestamp,
            "operating_minutes": operating,
            "idle_minutes": idle,
            "downtime_minutes": downtime,
            "utilization_percentage": utilization
        }

        table.put_item(Item=item)

        result = {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "period": period,
            "timestamp": timestamp,
            "operating_minutes": float(operating),
            "idle_minutes": float(idle),
            "downtime_minutes": float(downtime),
            "utilization_percentage": float(utilization)
        }

        return {
            "statusCode": 200,
            "body": json.dumps(result)
        }

    except ValueError as exc:
        return {
            "statusCode": 400,
            "body": json.dumps({
                "error": str(exc)
            })
        }

    except ClientError:
        logger.exception(
            "Error guardando utilización en DynamoDB"
        )
        raise
