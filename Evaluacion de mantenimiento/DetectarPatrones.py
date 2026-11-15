import json
import os
import uuid
from datetime import datetime, timedelta, timezone
import boto3
from boto3.dynamodb.conditions import Key

# Clientes AWS
dynamodb = boto3.resource("dynamodb")
sagemaker = boto3.client("sagemaker-runtime")

# [CORREGIDO]: Nombre de tabla alineado con el diagrama oscuro ("t_patrones"), configurable por env var
ALERTS_TABLE_NAME = os.environ.get("ALERTS_TABLE_NAME", "t_anomalias")
PATTERNS_TABLE_NAME = os.environ.get("PATTERNS_TABLE_NAME", "t_patrones")
SAGEMAKER_ENDPOINT = os.environ["SAGEMAKER_ENDPOINT"]

alerts_table = dynamodb.Table(ALERTS_TABLE_NAME)
patterns_table = dynamodb.Table(PATTERNS_TABLE_NAME)

# GSI usado para consultar anomalías recientes por tenant y rango de tiempo
INDEX_NAME = "tenant-timestamp-index"


def lambda_handler(event, context):
    try:
        tenant_id = event.get("tenant_id")
        current_machine_id = event.get("machine_id")

        if not tenant_id or not current_machine_id:
            raise ValueError("Falta tenant_id o machine_id en el payload")

        analysis_window_minutes = int(event.get("analysis_window_minutes", 15))

        # Calcular ventana de tiempo
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=analysis_window_minutes)
        start_timestamp = start_time.isoformat()
        end_timestamp = end_time.isoformat()

        # Consultar anomalías recientes en DynamoDB
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

        # Preparar observaciones para SageMaker (modelo de clustering)
        instances = []
        for alert in anomalies:
            telemetry = alert.get("telemetria", {})
            core_features = [
                telemetry.get("temperature"),
                telemetry.get("vibration"),
                telemetry.get("pressure")
            ]

            if any(value is None for value in core_features):
                continue

            # [CORREGIDO]: Si fuel_consumption no viene en algún registro antiguo, usa 0.0 en vez de descartar la alerta
            fuel = telemetry.get("fuel_consumption", 0.0)
            features = [float(v) for v in core_features] + [float(fuel if fuel is not None else 0.0)]

            instances.append({
                "tenant_id": tenant_id,
                "machine_id": alert.get("machine_id"),
                "alert_id": alert.get("alert_id"),
                "timestamp": alert.get("timestamp"),
                "features": features
            })

        # [CORREGIDO]: Variables alineadas con el contrato del PDF (incluye affected_machines, affected_zone y confidence)
        pattern_detected = False
        pattern_type = None
        affected_machines = []
        machine_data = event.get("machine_data", {})
        affected_zone = event.get("affected_zone") or machine_data.get("zone", "ZONE-A")
        confidence = 0.0

        # Si hay suficientes observaciones, enviamos a SageMaker
        if len(instances) >= 2:
            payload = {"instances": instances}

            ml_response = sagemaker.invoke_endpoint(
                EndpointName=SAGEMAKER_ENDPOINT,
                ContentType="application/json",
                Body=json.dumps(payload)
            )

            result = json.loads(ml_response["Body"].read().decode("utf-8"))
            assignments = result.get("assignments", [])

            # Agrupar máquinas por clúster
            clusters = {}
            for assignment in assignments:
                cluster_id = assignment.get("cluster_id")
                if cluster_id == -1:
                    continue
                clusters.setdefault(cluster_id, []).append(assignment)

            # Filtrar clústeres reales y guardar en DynamoDB (t_patrones)
            for cluster_id, members in clusters.items():
                machine_ids = sorted(list(set(item["machine_id"] for item in members)))

                if len(machine_ids) >= 2:
                    pattern_id = str(uuid.uuid4())
                    # [CORREGIDO]: Cálculo dinámico de confianza según máquinas en el clúster (antes faltaba y caía en 0.60)
                    cluster_confidence = min(0.99, round(0.70 + 0.05 * len(machine_ids), 2))

                    pattern = {
                        "tenant_id": tenant_id,
                        "sk": pattern_id,
                        "pattern_id": pattern_id,
                        "cluster_id": int(cluster_id),
                        "pattern_type": "SHARED_ANOMALOUS_BEHAVIOR",
                        "machine_ids": machine_ids,
                        "affected_zone": affected_zone,
                        "confidence": str(cluster_confidence),
                        "alert_ids": [item["alert_id"] for item in members],
                        "machines_affected": len(machine_ids),
                        "start_time": start_timestamp,
                        "end_time": end_timestamp,
                        "detected_at": datetime.now(timezone.utc).isoformat()
                    }
                    patterns_table.put_item(Item=pattern)

                    if current_machine_id in machine_ids:
                        pattern_detected = True
                        pattern_type = "shared_anomalous_behavior"
                        affected_machines = machine_ids
                        confidence = cluster_confidence

        pattern_result = {
            "tenant_id": tenant_id,
            "machine_id": current_machine_id,
            "pattern_detected": pattern_detected,
            "pattern_type": pattern_type,
            "affected_machines": affected_machines,
            "affected_zone": affected_zone,
            "confidence": confidence
        }

        # [CORREGIDO]: Preserva anomaly_result completo (incluyendo lista 'anomalies') y machine_data
        # para evitar pérdida de contexto en Step Functions
        anomaly_result = event.get("anomaly_result") or {
            "tenant_id": tenant_id,
            "machine_id": current_machine_id,
            "timestamp": event.get("timestamp"),
            "anomaly_detected": event.get("anomaly_detected", False),
            "anomalies": event.get("anomalies", [])
        }

        salida = {
            **pattern_result,
            "anomaly_result": anomaly_result,
            "pattern_result": pattern_result,
            "machine_data": machine_data
        }

        return salida

    except Exception as e:
        print(f"Error detecting patterns: {str(e)}")
        raise e
