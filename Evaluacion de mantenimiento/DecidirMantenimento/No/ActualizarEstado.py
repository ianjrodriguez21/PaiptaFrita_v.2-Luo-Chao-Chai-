import os
import json
import boto3
from datetime import datetime, timezone
from decimal import Decimal

dynamodb = boto3.resource("dynamodb")

MACHINES_TABLE = os.environ["MACHINES_TABLE_NAME"]
table = dynamodb.Table(MACHINES_TABLE)


def lambda_handler(event, context):
    try:
        tenant_id = event.get("tenant_id")
        machine_id = event.get("machine_id")
        risk_score = event.get("risk_score")
        recommended_state = event.get("recommended_state")

        # Validar datos obligatorios
        if not tenant_id or not machine_id:
            raise ValueError("tenant_id and machine_id are required")

        if risk_score is None or recommended_state is None:
            raise ValueError("risk_score and recommended_state are required")

        # Solo actualizar máquinas sin necesidad de mantenimiento
        if recommended_state != "normal" or float(risk_score) >= 30:
            raise ValueError(
                "Machine does not qualify for normal status update"
            )

        timestamp = datetime.now(timezone.utc).isoformat()

        # Actualizar registro de la máquina
        response = table.update_item(
            Key={
                "tenant_id": tenant_id,
                "machine_id": machine_id
            },
            UpdateExpression="""
                SET current_status = :status,
                    last_evaluation = :evaluation
            """,
            ExpressionAttributeValues={
                ":status": "operational",
                ":evaluation": {
                    "risk_score": Decimal(str(risk_score)),
                    "recommended_state": "normal",
                    "maintenance_recommended": False,
                    "evaluated_at": timestamp
                }
            },
            ReturnValues="ALL_NEW"
        )

        return {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "current_status": "operational",
            "risk_score": float(risk_score),
            "maintenance_recommended": False,
            "status_updated": True,
            "updated_at": timestamp
        }

    except Exception as e:
        print(f"Error updating machine status: {str(e)}")
        raise
