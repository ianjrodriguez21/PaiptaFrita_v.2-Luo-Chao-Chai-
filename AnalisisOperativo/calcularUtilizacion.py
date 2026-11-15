import json
import boto3
import os
from decimal import Decimal, ROUND_HALF_UP

# Inicializar cliente de DynamoDB
dynamodb = boto3.resource('dynamodb')

# Nombre de la tabla inyectado por serverless.yml
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', 't_utilizacion')

def lambda_handler(event, context):
    try:
        # Obtener los datos del evento
        body = json.loads(event['body']) if 'body' in event else event
        
        tenant_id = body.get('tenant_id')
        machine_id = body.get('machine_id')
        period = body.get('period') # Ej: "2026-11-14"
        
        operating_minutes = body.get('operating_minutes', 0)
        idle_minutes = body.get('idle_minutes', 0)
        downtime_minutes = body.get('downtime_minutes', 0)
        
        # Validar campos requeridos
        if not all([tenant_id, machine_id, period]):
            return {
                'statusCode': 400,
                'body': json.dumps('Error: Faltan campos requeridos (tenant_id, machine_id, period)')
            }
            
        # Calcular el porcentaje de utilización
        total_minutes = operating_minutes + idle_minutes + downtime_minutes
        
        if total_minutes > 0:
            utilization_raw = (operating_minutes / total_minutes) * 100
            utilization_percentage = Decimal(str(utilization_raw)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        else:
            utilization_percentage = Decimal('0.00')
            
        # Preparar el registro para DynamoDB
        item = {
            'pk': tenant_id,
            'sk': f"{machine_id}#{period}",
            'tenant_id': tenant_id,
            'machine_id': machine_id,
            'period': period,
            'operating_minutes': operating_minutes,
            'idle_minutes': idle_minutes,
            'downtime_minutes': downtime_minutes,
            'utilization_percentage': utilization_percentage
        }
        
        # Guardar en DynamoDB
        table = dynamodb.Table(DYNAMODB_TABLE_NAME)
        table.put_item(Item=item)
        
        # Formatear la salida
        salida_json = {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "period": period,
            "operating_minutes": operating_minutes,
            "idle_minutes": idle_minutes,
            "downtime_minutes": downtime_minutes,
            "utilization_percentage": float(utilization_percentage)
        }
        
        return {
            'statusCode': 200,
            'body': json.dumps(salida_json)
        }

    except Exception as e:
        print(f"Error calculando utilización: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error interno: {str(e)}')
        }
