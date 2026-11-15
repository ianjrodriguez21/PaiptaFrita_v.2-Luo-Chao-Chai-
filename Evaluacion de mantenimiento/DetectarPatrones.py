
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

# AWS clients
dynamodb = boto3.resource("dynamodb")
sagemaker = boto3.client("sagemaker-runtime")

# Environment variables
ALERTS_TABLE_NAME = os.environ.get(
    "ALERTS_TABLE_NAME", "t_anomalias"
)

PATTERNS_TABLE_NAME = os.environ.get(
    "PATTERNS_TABLE_NAME", "t_patrones_detectados"
)

SAGEMAKER_ENDPOINT = os.environ["SAGEMAKER_ENDPOINT"]

# DynamoDB tables
alerts_table = dynamodb.Table(ALERTS_TABLE_NAME)
patterns_table = dynamodb.Table(PATTERNS_TABLE_NAME)

# GSI used to query anomalies by tenant and timestamp
INDEX_NAME = "tenant-timestamp-index"


def lambda_handler(event, context):

    try:
        # 1. Read input
        if "body" in event and isinstance(event["body"], str):
            event = json.loads(event["body"])

        tenant_id = event["tenant_id"]

        analysis_window_minutes = int(
            event.get("analysis_window_minutes", 15)
        )

        if analysis_window_minutes <= 0:
            raise ValueError("analysis_window_minutes must be positive")

        # 2. Calculate analysis time window
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(
            minutes=analysis_window_minutes
        )

        start_timestamp = start_time.isoformat()
        end_timestamp = end_time.isoformat()

        # 3. Query anomalies from DynamoDB
        anomalies = []
        last_key = None

        while True:
            query_params = {
                "IndexName": INDEX_NAME,
                "KeyConditionExpression": (
                    Key("tenant_id").eq(tenant_id)
                    & Key("timestamp").between(
                        start_timestamp,
                        end_timestamp
                    )
                )
            }

            if last_key:
                query_params["ExclusiveStartKey"] = last_key

            response = alerts_table.query(**query_params)

            anomalies.extend(response.get("Items", []))

            last_key = response.get("LastEvaluatedKey")

            if not last_key:
                break

        # 4. Prepare observations for SageMaker
        instances = []

        for alert in anomalies:

            telemetry = alert.get("telemetria", {})

            features = [
                telemetry.get("temperature"),
                telemetry.get("vibration"),
                telemetry.get("pressure"),
                telemetry.get("fuel_consumption")
            ]

            # Skip incomplete observations
            if any(value is None for value in features):
                continue

            instances.append({
                "tenant_id": tenant_id,
                "machine_id": alert.get("machine_id"),
                "alert_id": alert.get("alert_id"),
                "timestamp": alert.get("timestamp"),
                "latitude": alert.get("latitude"),
                "longitude": alert.get("longitude"),
                "features": [float(value) for value in features]
            })

        if len(instances) < 2:
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "tenant_id": tenant_id,
                    "pattern_detected": False,
                    "patterns_found": 0,
                    "machines_analyzed": len(instances),
                    "reason": "Not enough observations"
                })
            }

        # 5. Send observations to SageMaker
        payload = {
            "instances": instances
        }

        ml_response = sagemaker.invoke_endpoint(
            EndpointName=SAGEMAKER_ENDPOINT,
            ContentType="application/json",
            Body=json.dumps(payload)
        )

        result = json.loads(
            ml_response["Body"].read().decode("utf-8")
        )

        # Expected response:
        # {
        #   "assignments": [
        #     {
        #       "machine_id": "MAC-102",
        #       "alert_id": "ALERT-001",
        #       "cluster_id": 0
        #     }
        #   ]
        # }

        assignments = result.get("assignments", [])

        # 6. Group machines by cluster
        clusters = {}

        for assignment in assignments:

            cluster_id = assignment.get("cluster_id")

            # -1 means noise / no detected cluster
            if cluster_id == -1:
                continue

            clusters.setdefault(cluster_id, []).append(
                assignment
            )

        detected_patterns = []

        # 7. Keep clusters involving at least two machines
        for cluster_id, members in clusters.items():

            machine_ids = list(set(
                item["machine_id"] for item in members
            ))

            if len(machine_ids) < 2:
                continue

            pattern_id = str(uuid.uuid4())

            pattern = {
                "tenant_id": tenant_id,
                "pattern_id": pattern_id,
                "cluster_id": int(cluster_id),
                "pattern_type": "SHARED_ANOMALOUS_BEHAVIOR",
                "machine_ids": machine_ids,
                "alert_ids": [
                    item["alert_id"] for item in members
                ],
                "machines_affected": len(machine_ids),
                "alerts_analyzed": len(members),
                "start_time": start_timestamp,
                "end_time": end_timestamp,
                "detected_at": datetime.now(
                    timezone.utc
                ).isoformat()
            }

            # 8. Save pattern in DynamoDB
            patterns_table.put_item(Item=pattern)

            detected_patterns.append(pattern)

        # 9. Return results
        return {
            "statusCode": 200,
            "body": json.dumps({
                "tenant_id": tenant_id,
                "pattern_detected": len(detected_patterns) > 0,
                "patterns_found": len(detected_patterns),
                "machines_analyzed": len(instances),
                "patterns": detected_patterns
            })
        }

    except Exception as e:

        print(f"Error detecting patterns: {str(e)}")

        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": str(e)
            })
        }
