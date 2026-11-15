
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
        # 1. Recibir telemetría
        if 'body' in event:
            telemetry = json.loads(event['body'])
        else:
            telemetry = event

        tenant_id = telemetry.get('tenant_id')
        machine_id = telemetry.get('machine_id')

        if not tenant_id or not machine_id:
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': 'Falta tenant_id o machine_id'
                })
            }

        # 2. Extraer variables para el modelo
        features = [
            float(telemetry['temperatura_motor']),
            float(telemetry['velocidad']),
            float(telemetry['combustible'])
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
        prediction = json.loads(
            response['Body'].read().decode('utf-8')
        )

        # El modelo devuelve:
        # -1 = Anomalía
        #  1 = Normal

        is_anomaly = prediction['prediction'] == -1

        anomaly_score = prediction.get('anomaly_score')

        # 5. Registrar anomalía si corresponde
        if is_anomaly:

            table = dynamodb.Table(ALERTS_TABLE_NAME)

            alert = {
                'tenant_id': tenant_id,
                'alert_id': str(uuid.uuid4()),
                'machine_id': machine_id,
                'timestamp': telemetry.get(
                    'timestamp',
                    datetime.utcnow().isoformat()
                ),
                'tipo': 'ANOMALIA_DETECTADA',
                'telemetria': json.loads(
                    json.dumps(telemetry),
                    parse_float=Decimal
                ),
                'anomaly_score': Decimal(str(anomaly_score))
                    if anomaly_score is not None else None
            }

            table.put_item(Item=alert)

        # 6. Devolver resultado
        return {
            'statusCode': 200,
            'body': json.dumps({
                'machine_id': machine_id,
                'anomaly_detected': is_anomaly,
                'anomaly_score': anomaly_score,
                'message': (
                    'Anomalia detectada'
                    if is_anomaly
                    else 'Funcionamiento normal'
                )
            })
        }

    except Exception as e:

        print(f"Error detectando anomalias: {str(e)}")

        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e)
            })
        }
