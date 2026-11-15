
import json
import os
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")

# Tables
TELEMETRY_TABLE_NAME = os.environ.get(
    "TELEMETRY_TABLE_NAME",
    "t_telemetria_historica"
)

ANOMALIES_TABLE_NAME = os.environ.get(
    "ANOMALIES_TABLE_NAME",
    "t_anomalias"
)

INCIDENTS_TABLE_NAME = os.environ.get(
    "INCIDENTS_TABLE_NAME",
    "t_incidentes"
)

telemetry_table = dynamodb.Table(TELEMETRY_TABLE_NAME)
anomalies_table = dynamodb.Table(ANOMALIES_TABLE_NAME)
incidents_table = dynamodb.Table(INCIDENTS_TABLE_NAME)

INDEX_NAME = "tenant-machine-timestamp-index"


def query_machine_records(table, tenant_id, machine_id,
                          start_time, end_time):

    records = []
    last_key = None

    # Sort key format: machine_id#timestamp
    start_key = f"{machine_id}#{start_time}"
    end_key = f"{machine_id}#{end_time}"

    while True:

        params = {
            "IndexName": INDEX_NAME,
            "KeyConditionExpression": (
                Key("tenant_id").eq(tenant_id)
                & Key("machine_timestamp").between(
                    start_key, end_key
                )
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

    # Telemetry can be stored inside "telemetria"
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

    percentage = (
        (new_average - old_average)
        / abs(old_average)
    ) * 100

    return round(percentage, 2)


def lambda_handler(event, context):

    try:

        # 1. Read input
        if "body" in event and isinstance(event["body"], str):
            event = json.loads(event["body"])

        tenant_id = event["tenant_id"]
        machine_id = event["machine_id"]

        analysis_window_days = int(
            event.get("analysis_window_days", 30)
        )

        if analysis_window_days <= 0:
            raise ValueError(
                "analysis_window_days must be positive"
            )

        # 2. Calculate historical period
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(
            days=analysis_window_days
        )

        start_timestamp = start_time.isoformat()
        end_timestamp = end_time.isoformat()

        # 3. Retrieve historical telemetry
        telemetry_records = query_machine_records(
            telemetry_table,
            tenant_id,
            machine_id,
            start_timestamp,
            end_timestamp
        )

        # 4. Retrieve previous anomalies
        anomaly_records = query_machine_records(
            anomalies_table,
            tenant_id,
            machine_id,
            start_timestamp,
            end_timestamp
        )

        # 5. Retrieve previous incidents
        incident_records = query_machine_records(
            incidents_table,
            tenant_id,
            machine_id,
            start_timestamp,
            end_timestamp
        )

        if not telemetry_records:
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "tenant_id": tenant_id,
                    "machine_id": machine_id,
                    "historical_pattern_detected": False,
                    "reason": "No historical telemetry found"
                })
            }

        # 6. Sort telemetry chronologically
        telemetry_records.sort(
            key=lambda x: x["timestamp"]
        )

        variables = [
            "temperature",
            "vibration",
            "pressure",
            "fuel_consumption"
        ]

        findings = []
        trends = {}

        # 7. Analyze trends
        for variable in variables:

            values = [
                get_value(record, variable)
                for record in telemetry_records
            ]

            values = [
                value for value in values
                if value is not None
            ]

            if len(values) < 2:
                continue

            trend = calculate_trend(values)
            trends[variable] = trend

            # Initial threshold for increasing trends
            if trend >= 15:
                findings.append(
                    f"{variable}_increasing"
                )

            elif trend <= -15:
                findings.append(
                    f"{variable}_decreasing"
                )

        # 8. Count historical anomalies and incidents
        similar_incidents = len(incident_records)
        previous_anomalies = len(anomaly_records)

        # 9. Calculate an initial historical risk score
        trend_risk = min(
            len([
                value for value in trends.values()
                if value >= 15
            ]) * 15,
            45
        )

        anomaly_risk = min(
            previous_anomalies * 3,
            30
        )

        incident_risk = min(
            similar_incidents * 5,
            25
        )

        historical_risk_score = min(
            trend_risk + anomaly_risk + incident_risk,
            100
        )

        # 10. Determine whether a historical pattern exists
        recurrence_detected = (
            previous_anomalies >= 2
            or similar_incidents >= 2
        )

        historical_pattern_detected = (
            len(findings) > 0
            or recurrence_detected
        )

        # 11. Return results
        result = {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "historical_pattern_detected":
                historical_pattern_detected,
            "findings": findings,
            "trends_percentage": trends,
            "similar_incidents": similar_incidents,
            "previous_anomalies": previous_anomalies,
            "recurrence_detected": recurrence_detected,
            "historical_risk_score": historical_risk_score,
            "records_analyzed": len(telemetry_records),
            "analysis_period_days": analysis_window_days
        }

        return {
            "statusCode": 200,
            "body": json.dumps(result)
        }

    except Exception as e:

        print(f"Historical analysis error: {str(e)}")

        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": str(e)
            })
        }
