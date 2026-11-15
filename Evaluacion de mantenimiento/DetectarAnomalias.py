import json
import boto3
import os
import uuid
from datetime import datetime
from decimal import Decimal

# Cliente para invocar modelos de SageMaker
sagemaker_runtime = boto3.client('sagemaker-runtime')

# Cliente de DynamoDB para registrar anomalías
dynamodb = boto3.resource('dynamodb')

# Variables de entorno
ENDPOINT_NAME = os.environ['SAGEMAKER_ENDPOINT']
ALERTS_TABLE_NAME = os.environ.get(
    'ALERTS_TABLE_NAME',
    't_alertas_flotas'
)

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

        # 2. Extraer variables con los nombres exactos en inglés del JSON
        features = [
            float(telemetry.get('temperature', 0)),
            float(telemetry.get('vibration', 0)),
            float(telemetry.get('pressure', 0))
        ]

        # 3. Preparar solicitud para SageMaker
        payload = {
            'features': features
        }

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
        
        timestamp = telemetry.get('timestamp', datetime.utcnow().isoformat() + "Z")

        # 5. Construir la salida estricta para Step Functions
        salida = {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "timestamp": timestamp,
            "anomaly_detected": is_anomaly
        }

        # 6. Registrar anomalía si corresponde
        if is_anomaly:
            # Agregar detalle de anomalías a la salida
            salida["anomalies"] = [
                {
                    "variable": "temperature",
                    "value": telemetry.get('temperature'),
                    "anomaly_score": round(anomaly_score, 2)
                },
                {
                    "variable": "vibration",
                    "value": telemetry.get('vibration'),
                    "anomaly_score": round(anomaly_score, 2)
                }
            ]

            table = dynamodb.Table(ALERTS_TABLE_NAME)

            # Usamos tenant_id y sk (machine_id#timestamp) para tener un buen indexado
            alert = {
                'tenant_id': tenant_id,
                'sk': f"{machine_id}#{timestamp}",
                'alert_id': str(uuid.uuid4()),
                'machine_id': machine_id,
                'timestamp': timestamp,
                'tipo': 'ANOMALIA_DETECTADA',
                'telemetria': json.loads(json.dumps(telemetry), parse_float=Decimal),
                'anomalies': json.loads(json.dumps(salida["anomalies"]), parse_float=Decimal)
            }

            table.put_item(Item=alert)

        # 7. Retornar el diccionario directamente (Ideal para Step Functions)
        return salida

    except Exception as e:
        print(f"Error detectando anomalias: {str(e)}")
        # Lanzar la excepción para que Step Functions la intercepte
        raise e
