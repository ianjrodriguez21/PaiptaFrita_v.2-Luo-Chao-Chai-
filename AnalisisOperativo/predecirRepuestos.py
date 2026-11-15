import json
import boto3
import os

# Inicializar cliente de DynamoDB
dynamodb = boto3.resource('dynamodb')

# Nombre de la tabla inyectado por serverless.yml
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', 't_predictions')

def lambda_handler(event, context):
    try:
        # Obtener los datos del evento (entrada simulada para la hackathon)
        body = json.loads(event['body']) if 'body' in event else event
        
        tenant_id = body.get('tenant_id')
        prediction_period = body.get('prediction_period') # ej: "next_30_days"
        predictions = body.get('predictions', [])
        
        if not tenant_id or not prediction_period or not predictions:
            return {
                'statusCode': 400,
                'body': json.dumps('Error: Faltan campos requeridos (tenant_id, prediction_period, predictions)')
            }
            
        table = dynamodb.Table(DYNAMODB_TABLE_NAME)
        
        # Guardar cada predicción en la base de datos
        # Usamos batch_writer para optimizar la escritura múltiple en DynamoDB
        with table.batch_writer() as batch:
            for pred in predictions:
                part_id = pred.get('part_id')
                
                # Armamos el item según el diagrama de arquitectura[cite: 2, 24]
                item = {
                    'tenant_id': tenant_id,                         # PK
                    'sk': f"{part_id}#{prediction_period}",         # SK
                    'part_id': part_id,
                    'prediction_period': prediction_period,
                    'current_stock': pred.get('current_stock', 0),
                    'predicted_demand': pred.get('predicted_demand', 0),
                    'recommended_stock': pred.get('recommended_stock', 0),
                    'reorder_required': pred.get('reorder_required', False)
                }
                batch.put_item(Item=item)
        
        # Formatear la salida exacta solicitada en el PDF
        salida_json = {
            "tenant_id": tenant_id,
            "prediction_period": prediction_period,
            "predictions": predictions
        }
        
        return {
            'statusCode': 200,
            'body': json.dumps(salida_json)
        }

    except Exception as e:
        print(f"Error prediciendo repuestos: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error interno: {str(e)}')
        }
