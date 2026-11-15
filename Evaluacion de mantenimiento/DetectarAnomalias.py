import json
import boto3
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

# Cliente para invocar modelos de SageMaker
sagemaker_runtime = boto3.client('sagemaker-runtime')

# Cliente de DynamoDB para registrar anomalías
dynamodb = boto3.resource('dynamodb')

# Variables de entorno (Alineado con el diagrama: t_anomalias)
ENDPOINT_NAME = os.environ['SAGEMAKER_ENDPOINT']
ALERTS_TABLE_NAME = os.environ.get('ALERTS_TABLE_NAME', 't_anomalias')


def evaluate_anomalous_variables(telemetry, anomaly_score):
    """Identifica qué variables específicas superan umbrales operativos."""
    thresholds = {
        "temperature": 95.0,
        "vibration": 7.0,
        "pressure": 200.0,
        "fuel_consumption": 30.0
    }
    detected = []
    score = round(abs(float(anomaly_score)), 2)

    for var, limit in thresholds.items():
        val = telemetry.get(var)
        if val is not None and float(val) > limit:
            detected.append({
                "variable": var,
                "value": float(val),
                "anomaly_score": score
            })

    # Si SageMaker detectó anomalía multivariable pero ninguna superó el umbral individual
    if not detected:
        detected.append({
            "variable": "multivariate_behavior",
            "value": float(telemetry.get("temperature", 0)),
            "anomaly_score": score
        })

    return detected


def lambda_handler(event, context):
    try:
        # 1. Recibir telemetría (Adaptado para Step Functions o invocación directa)
        if 'body' in event:
            telemetry = json.loads(event['body'])
        else:
            telemetry = event

        tenant_id = telemetry.get('tenant_id')
        machine_id = telemetry.get('machine_id')

        if not tenant_id or not machine_id:
            raise ValueError("Falta tenant_id o machine_id")

        # 2. Extraer variables para SageMaker
        features = [
            float(telemetry.get('temperature', 0)),
            float(telemetry.get('vibration', 0)),
            float(telemetry.get('pressure', 0))
        ]

        # 3. Preparar solicitud para SageMaker
        payload = {'features': features}

        response = sagemaker_runtime.invoke_endpoint(
            EndpointName=ENDPOINT_NAME,
            ContentType='application/json',
            Accept='application/json',
            Body=json.dumps(payload)
        )

        # 4. Obtener predicción del modelo
        prediction = json.loads(response['Body'].read().decode('utf-8'))

        # El modelo devuelve: -1 = Anomalía, 1 = Normal
        is_anomaly = prediction.get('prediction') == -1
        anomaly_score = prediction.get('anomaly_score', 0.0)

        timestamp = telemetry.get('timestamp', datetime.now(timezone.utc).isoformat())

        # 5. Construir la salida estricta para Step Functions
        salida = {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "timestamp": timestamp,
            "anomaly_detected": is_anomaly,
            "anomalies": []
        }

        # 6. Registrar anomalía si corresponde
        if is_anomaly:
            salida["anomalies"] = evaluate_anomalous_variables(telemetry, anomaly_score)

            table = dynamodb.Table(ALERTS_TABLE_NAME)
            machine_ts = f"{machine_id}#{timestamp}"

            # Guardamos 'sk' y 'machine_timestamp' para soportar los GSI de las Lambdas 4 y 5
            alert = {
                'tenant_id': tenant_id,
                'sk': machine_ts,
                'machine_timestamp': machine_ts,
                'alert_id': str(uuid.uuid4()),
                'machine_id': machine_id,
                'timestamp': timestamp,
                'tipo': 'ANOMALIA_DETECTADA',
                'telemetria': json.loads(json.dumps(telemetry), parse_float=Decimal),
                'anomalies': json.loads(json.dumps(salida["anomalies"]), parse_float=Decimal)
            }

            table.put_item(Item=alert)

        # 7. Retornar el diccionario directamente para Step Functions
        return salida

    except Exception as e:
        print(f"Error detectando anomalias: {str(e)}")
        raise e
