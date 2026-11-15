import os
from datetime import datetime, timedelta, timezone
import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")

# Tables
TELEMETRY_TABLE_NAME = os.environ.get("TELEMETRY_TABLE_NAME", "t_telemetria_historica")
ANOMALIES_TABLE_NAME = os.environ.get("ANOMALIES_TABLE_NAME", "t_anomalias")
INCIDENTS_TABLE_NAME = os.environ.get("INCIDENTS_TABLE_NAME", "t_incidentes")

telemetry_table = dynamodb.Table(TELEMETRY_TABLE_NAME)
anomalies_table = dynamodb.Table(ANOMALIES_TABLE_NAME)
incidents_table = dynamodb.Table(INCIDENTS_TABLE_NAME)

INDEX_NAME = "tenant-machine-timestamp-index"

def query_machine_records(table, tenant_id, machine_id, start_time, end_time):
    records = []
    last_key = None
    start_key = f"{machine_id}#{start_time}"
    end_key = f"{machine_id}#{end_time}"

    while True:
        params = {
            "IndexName": INDEX_NAME,
            "KeyConditionExpression": (
                Key("tenant_id").eq(tenant_id)
                & Key("machine_timestamp").between(start_key, end_key)
            )
        }
        if last_key:
            params["ExclusiveStartKey"] = last_key

        response = table.query(**params)
        records.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")

        if not last_key:
            break
    return records

def get_value(record, variable):
    telemetry = record.get("telemetria", record)
    value = telemetry.get(variable)
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

def calculate_trend(values):
    if len(values) < 2:
        return 0

    middle = len(values) // 2
    first_half = values[:middle]
    second_half = values[middle:]

    if not first_half or not second_half:
        return 0

    old_average = sum(first_half) / len(first_half)
    new_average = sum(second_half) / len(second_half)

    if old_average == 0:
        return 0

    percentage = ((new_average - old_average) / abs(old_average)) * 100
    return round(percentage, 2)

def lambda_handler(event, context):
    try:
        # 1. Leer input directamente del evento (Step Functions)
        tenant_id = event.get("tenant_id")
        machine_id = event.get("machine_id")

        if not tenant_id or not machine_id:
            raise ValueError("Falta tenant_id o machine_id en el payload")

        analysis_window_days = int(event.get("analysis_window_days", 30))

        # 2. Calcular periodo histórico
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=analysis_window_days)
        start_timestamp = start_time.isoformat()
        end_timestamp = end_time.isoformat()

        # 3. Recuperar datos históricos
        telemetry_records = query_machine_records(
            telemetry_table, tenant_id, machine_id, start_timestamp, end_timestamp
        )
        anomaly_records = query_machine_records(
            anomalies_table, tenant_id, machine_id, start_timestamp, end_timestamp
        )
        incident_records = query_machine_records(
            incidents_table, tenant_id, machine_id, start_timestamp, end_timestamp
        )

        # 4. Analizar patrones
        pattern_detected = False
        pattern_type = None

        if telemetry_records:
            telemetry_records.sort(key=lambda x: x.get("timestamp", ""))

            variables = ["temperature", "vibration", "pressure", "fuel_consumption"]
            degradacion_detectada = False

            for variable in variables:
                values = [get_value(record, variable) for record in telemetry_records]
                values = [v for v in values if v is not None]

                if len(values) < 2:
                    continue

                trend = calculate_trend(values)
                
                # Si hay una tendencia de aumento sostenida (ej. >= 15%), asumimos degradación
                if trend >= 15:
                    degradacion_detectada = True
                    break

            similar_incidents = len(incident_records)
            previous_anomalies = len(anomaly_records)

            # Mapear los hallazgos a los tipos de patrones exigidos en el PDF
            if degradacion_detectada:
                pattern_detected = True
                pattern_type = "degradación_progresiva"
            elif previous_anomalies >= 3 or similar_incidents >= 2:
                pattern_detected = True
                pattern_type = "picos_frecuentes"

        # 5. Retornar el formato exacto para la siguiente etapa en Step Functions
        salida = {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "pattern_detected": pattern_detected,
            "pattern_type": pattern_type
        }
        
        # Propagar cualquier otra variable útil que viniera del evento anterior (ej. anomaly_detected)
        # Esto permite que la Step Function tenga todo el contexto
        if "anomaly_detected" in event:
            salida["anomaly_detected"] = event["anomaly_detected"]

        return salida

    except Exception as e:
        print(f"Error en analisis historico (DetectarPatrones): {str(e)}")
        raise e
