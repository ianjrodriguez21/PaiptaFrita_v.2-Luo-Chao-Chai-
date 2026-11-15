import json
import boto3
import os
from datetime import datetime
from decimal import Decimal

# Inicializar cliente de DynamoDB
dynamodb = boto3.resource('dynamodb')

# Nombre de la tabla inyectado por serverless.yml
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', 't_efficacy')

def lambda_handler(event, context):
    try:
        # Obtener los datos del evento
        body = json.loads(event['body']) if 'body' in event else event
        
        tenant_id = body.get('tenant_id')
        fecha = body.get('fecha', datetime.utcnow().strftime('%Y-%m-%d'))
        maquinas_utilizacion = body.get('machines_utilization', [])
        
        if not tenant_id or not maquinas_utilizacion:
            return {
                'statusCode': 400,
                'body': json.dumps('Error: Faltan campos requeridos (tenant_id, machines_utilization)')
            }
            
        fleet_analysis = []
        
        # Clasificar cada máquina según su utilización
        for maquina in maquinas_utilizacion:
            machine_id = maquina.get('machine_id')
            utilization = float(maquina.get('utilization_percentage', 0))
            
            # Lógica de clasificación (puedes ajustar los umbrales para tu hackathon)
            if utilization >= 85.0:
                classification = "overutilized"
            elif utilization <= 40.0:
                classification = "underutilized"
            else:
                classification = "normal"
                
            fleet_analysis.append({
                "machine_id": machine_id,
                "utilization_percentage": utilization,
                "classification": classification
            })
            
        # Preparar el registro para DynamoDB
        # Convertimos los floats a Decimal para DynamoDB
        fleet_analysis_dynamo = json.loads(json.dumps(fleet_analysis), parse_float=Decimal)
        
        item = {
            'tenant_id': tenant_id, # PK: tenant_id
            'fecha': fecha,         # SK: fecha[cite: 1, 2]
            'fleet_analysis': fleet_analysis_dynamo
        }
        
        # Guardar en DynamoDB
        table = dynamodb.Table(DYNAMODB_TABLE_NAME)
        table.put_item(Item=item)
        
        # Formatear la salida exacta solicitada en el PDF
        salida_json = {
            "tenant_id": tenant_id,
            "fleet_analysis": fleet_analysis
        }
        
        return {
            'statusCode': 200,
            'body': json.dumps(salida_json)
        }

    except Exception as e:
        print(f"Error analizando eficacia: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error interno: {str(e)}')
        }
