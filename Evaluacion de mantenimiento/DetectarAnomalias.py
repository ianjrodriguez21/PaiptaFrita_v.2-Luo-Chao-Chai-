
import json
import boto3
import os
import uuid
from datetime import datetime
from decimal import Decimal

# Clientes de AWS
sagemaker_runtime = boto3.client('sagemaker-runtime')
dynamodb = boto3.resource('dynamodb')

# Variables de entorno
ENDPOINT_NAME = os.environ.get('SAGEMAKER_ENDPOINT', 'tu-endpoint-sagemaker')
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', 't_anomalias')

def lambda_handler(event, context):
    try:
        # 1. Recibir telemetría (Adaptado para Step Functions o invocación directa)
        telemetry = event.get('body', event)
        if isinstance(telemetry, str):
            telemetry = json.loads(telemetry)

        tenant_id = telemetry.get('tenant_id')
        machine_id = telemetry.get('machine_id')

        if not tenant_id or not machine_id:
            raise ValueError("Falta tenant_id o machine_id en el payload")

        # 2. Extraer variables con los nombres exactos en inglés del JSON[cite: 5, 6]
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

        # Asumiendo que -1 es Anomalía y 1 es Normal
        is_anomaly = prediction.get('prediction') == -1
        anomaly_score = prediction.get('anomaly_score', 0.0)
        
        timestamp = telemetry.get('timestamp', datetime.utcnow().isoformat() + "Z")

        # 5. Construir la salida requerida para Step Functions[cite: 10, 11]
        salida = {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "timestamp": timestamp,
            "anomaly_detected": is_anomaly
        }

        # 6. Si hay anomalía, agregar detalles y guardar en DynamoDB
        if is_anomaly:
            anomalies_list = [
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
            salida["anomalies"] = anomalies_list

            # GUARDAR EN DYNAMODB (Para que DetectarPatrones lo use después)
            table = dynamodb.Table(DYNAMODB_TABLE_NAME)
            
            # Formatear item (Partition Key: tenant_id, Sort Key: machine_id#timestamp)
            item = {
                'tenant_id': tenant_id,
                'sk': f"{machine_id}#{timestamp}",
                'anomaly_id': str(uuid.uuid4()),
                'machine_id': machine_id,
                'timestamp': timestamp,
                # Convertimos a Decimal para compatibilidad con DynamoDB
                'anomalies': json.loads(json.dumps(anomalies_list), parse_float=Decimal),
                'telemetry_raw': json.loads(json.dumps(telemetry), parse_float=Decimal)
            }
            
            table.put_item(Item=item)

        # Retornamos el dict directamente para que fluya en el AWS Step Functions
        return salida

    except Exception as e:
        print(f"Error detectando anomalias: {str(e)}")
        # Propagar el error para que Step Functions lo maneje (Retry/Catch)
        raise e
