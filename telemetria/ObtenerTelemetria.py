import json
import boto3
import os
import uuid
from datetime import datetime
from decimal import Decimal

# Inicializar clientes de AWS
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

# Variables de entorno (configuradas en la consola de AWS Lambda)
S3_BUCKET_NAME = os.environ.get('S3_BUCKET_NAME', 'bucket-telemetria-historico')
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', 't_telemetria_actual')

def lambda_handler(event, context):
    try:
        # 1. Parsear el cuerpo de la solicitud desde API Gateway
        # API Gateway suele enviar el body como un string JSON
        if 'body' in event:
            telemetry_data = json.loads(event['body'])
        else:
            # Por si se invoca directamente con el JSON
            telemetry_data = event

        # 2. Validar campos requeridos mínimos
        tenant_id = telemetry_data.get('tenant_id')
        machine_id = telemetry_data.get('machine_id')

        if not tenant_id or not machine_id:
            return {
                'statusCode': 400,
                'body': json.dumps('Error: Faltan campos requeridos (tenant_id o machine_id)')
            }

        # Generar timestamp actual
        timestamp = datetime.utcnow().isoformat()
        
        # Añadir timestamp a los datos si no existe
        if 'timestamp' not in telemetry_data:
            telemetry_data['timestamp'] = timestamp

        # 3. Guardar en Amazon S3 (RawTelemetry - Histórico)
        # Crear un nombre de archivo único para evitar colisiones
        file_name = f"{tenant_id}/{machine_id}/{timestamp.replace(':', '-')}_{str(uuid.uuid4())[:8]}.json"
        
        s3_client.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=file_name,
            Body=json.dumps(telemetry_data),
            ContentType='application/json'
        )

        # 4. Guardar/Actualizar en Amazon DynamoDB (t_telemetria_actual)
        # DynamoDB requiere que los floats sean convertidos a Decimal
        dynamodb_item = json.loads(json.dumps(telemetry_data), parse_float=Decimal)
        
        # Ajustar la Sort Key (SK) como indica el diagrama: machine_id#timestamp
        # Aunque en tu texto pusiste "separation key: machine_id", el diagrama muestra 
        # PK: tenant_id, SK: machine_id#timestamp para la tabla t_telemetria_actual
        sort_key_value = f"{machine_id}#{timestamp}"
        dynamodb_item['sk'] = sort_key_value

        table = dynamodb.Table(DYNAMODB_TABLE_NAME)
        table.put_item(Item=dynamodb_item)

        # 5. Respuesta exitosa
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Telemetría procesada y guardada exitosamente',
                's3_path': file_name,
                'dynamodb_pk': tenant_id,
                'dynamodb_sk': sort_key_value
            })
        }

    except Exception as e:
        print(f"Error procesando telemetría: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error interno del servidor: {str(e)}')
        }
