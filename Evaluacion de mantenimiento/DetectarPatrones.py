import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key

# AWS clients
dynamodb = boto3.resource("dynamodb")
sagemaker = boto3.client("sagemaker-runtime")

# Environment variables
ALERTS_TABLE_NAME = os.environ.get("ALERTS_TABLE_NAME", "t_anomalias")
PATTERNS_TABLE_NAME = os.environ.get("PATTERNS_TABLE_NAME", "t_patrones_detectados")
SAGEMAKER_ENDPOINT = os.environ["SAGEMAKER_ENDPOINT"]

# DynamoDB tables
alerts_table = dynamodb.Table(ALERTS_TABLE_NAME)
patterns_table = dynamodb.Table(PATTERNS_TABLE_NAME)

# GSI used to query anomalies by tenant and timestamp
INDEX_NAME = "tenant-timestamp-index"

def lambda_handler(event, context):
    try:
        # 1. Leer input directamente de Step Functions
        tenant_id = event.get("tenant_id")
        current_machine_id = event.get("machine_id")

        if not tenant_id or not current_machine_id:
            raise ValueError("Falta tenant_id o machine_id en el payload")

        analysis_window_minutes = int(event.get("analysis_window_minutes", 15))

        # 2. Calcular ventana de tiempo
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=analysis_window_minutes)

        start_timestamp = start_time.isoformat()
        end_timestamp = end_time.isoformat()

        # 3. Consultar anomalías recientes en DynamoDB
        anomalies = []
        last_key = None

        while True:
            query_params = {
                "IndexName": INDEX_NAME,
                "KeyConditionExpression": (
                    Key("tenant_id").eq(tenant_id)
                    & Key("timestamp").between(start_timestamp, end_timestamp)
                )
            }

            if last_key:
                query_params["ExclusiveStartKey"] = last_key

            response = alerts_table.query(**query_params)
            anomalies.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")

            if not last_key:
                break

        # 4. Preparar observaciones para SageMaker
        instances = []
        for alert in anomalies:
            telemetry = alert.get("telemetria", {})
            features = [
                telemetry.get("temperature"),
                telemetry.get("vibration"),
                telemetry.get("pressure"),
                telemetry.get("fuel_consumption")
            ]

            if any(value is None for value in features):
                continue

            instances.append({
                "tenant_id": tenant_id,
                "machine_id": alert.get("machine_id"),
                "alert_id": alert.get("alert_id"),
                "timestamp": alert.get("timestamp"),
                "features": [float(value) for value in features]
            })

        # Variables para la respuesta de la Step Function
        pattern_detected = False
        pattern_type = None

        # 5. Si hay suficientes datos, enviamos a SageMaker
        if len(instances) >= 2:
            payload = {
                "instances": instances
            }

            ml_response = sagemaker.invoke_endpoint(
                EndpointName=SAGEMAKER_ENDPOINT,
                ContentType="application/json",
                Body=json.dumps(payload)
            )

            result = json.loads(ml_response["Body"].read().decode("utf-8"))
            assignments = result.get("assignments", [])

            # 6. Agrupar máquinas por clúster
            clusters = {}
            for assignment in assignments:
                cluster_id = assignment.get("cluster_id")
                if cluster_id == -1:
                    continue
                clusters.setdefault(cluster_id, []).append(assignment)

            # 7. Filtrar clústeres reales y guardar en DynamoDB
            for cluster_id, members in clusters.items():
                machine_ids = list(set(item["machine_id"] for item in members))

                if len(machine_ids) >= 2:
                    pattern_id = str(uuid.uuid4())
                    pattern = {
                        "tenant_id": tenant_id,
                        "pattern_id": pattern_id,
                        "cluster_id": int(cluster_id),
                        "pattern_type": "SHARED_ANOMALOUS_BEHAVIOR",
                        "machine_ids": machine_ids,
                        "alert_ids": [item["alert_id"] for item in members],
                        "machines_affected": len(machine_ids),
                        "start_time": start_timestamp,
                        "end_time": end_timestamp,
                        "detected_at": datetime.now(timezone.utc).isoformat()
                    }
                    patterns_table.put_item(Item=pattern)

                    # Si la máquina que disparó este flujo pertenece al clúster anómalo, lo marcamos
                    if current_machine_id in machine_ids:
                        pattern_detected = True
                        pattern_type = "comportamiento_anomalo_compartido"

        # 8. Retornar diccionario plano EXACTO para la siguiente Lambda en Step Functions
        salida = {
            "tenant_id": tenant_id,
            "machine_id": current_machine_id,
            "pattern_detected": pattern_detected,
            "pattern_type": pattern_type
        }

        # Propagamos si hubo anomalía en el paso anterior para no perder el contexto
        if "anomaly_detected" in event:
            salida["anomaly_detected"] = event["anomaly_detected"]

        return salida

    except Exception as e:
        print(f"Error detecting patterns: {str(e)}")
        # Lanzamos el error para que la máquina de estados pueda reintentar (Retry/Catch)
        raise e
