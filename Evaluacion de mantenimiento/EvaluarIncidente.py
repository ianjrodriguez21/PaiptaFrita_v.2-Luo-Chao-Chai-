import json

def unwrap_result(value):
    """
    Allows the function to receive either:
    - A normal dictionary
    - A Lambda proxy response with statusCode and body
    """
    if isinstance(value, str):
        value = json.loads(value)

    if isinstance(value, dict) and "body" in value:
        body = value["body"]

        if isinstance(body, str):
            return json.loads(body)

        if isinstance(body, dict):
            return body

    return value or {}


def normalize_score(value):
    """Convert a score from 0-1 or 0-100 into 0-100."""
    if value is None:
        return 0

    score = float(value)

    if 0 <= score <= 1:
        score *= 100

    return max(0, min(score, 100))


def get_anomaly_score(anomaly_result):

    anomalies = anomaly_result.get("anomalies", [])

    if not anomalies:
        return 0, []

    scores = []
    variables = []

    for anomaly in anomalies:
        score = normalize_score(
            anomaly.get("anomaly_score", 0)
        )
        scores.append(score)

        variable = anomaly.get("variable")
        if variable:
            variables.append(variable)

    # Average severity of the detected anomalies
    average_score = sum(scores) / len(scores)

    # More simultaneous anomalies increase concern
    if len(anomalies) >= 3:
        average_score = min(average_score + 10, 100)

    return average_score, variables


def lambda_handler(event, context):

    try:
        # 1. Read previous Lambda results
        anomaly_result = unwrap_result(
            event.get("anomaly_result")
        )

        pattern_result = unwrap_result(
            event.get("pattern_result")
        )

        historical_result = unwrap_result(
            event.get("historical_result")
        )

        # 2. Identify machine
        tenant_id = (
            event.get("tenant_id")
            or anomaly_result.get("tenant_id")
        )

        machine_id = (
            event.get("machine_id")
            or anomaly_result.get("machine_id")
        )

        if not tenant_id or not machine_id:
            raise ValueError(
                "tenant_id and machine_id are required"
            )

        # 3. Calculate current anomaly score
        anomaly_score, variables = get_anomaly_score(
            anomaly_result
        )

        # 4. Evaluate shared patterns
        pattern_detected = pattern_result.get(
            "pattern_detected", False
        )

        if pattern_detected:
            confidence = normalize_score(
                pattern_result.get("confidence", 0.60)
            )
            pattern_score = confidence
        else:
            pattern_score = 0

        # 5. Get historical risk
        historical_score = normalize_score(
            historical_result.get(
                "historical_risk_score", 0
            )
        )

        # 6. Calculate combined risk
        risk_score = (
            anomaly_score * 0.50
            + pattern_score * 0.20
            + historical_score * 0.30
        )

        risk_score = round(
            max(0, min(risk_score, 100))
        )

        # 7. Classify incident
        if risk_score >= 70:
            severity = "critical"
            priority = "urgent"
            recommended_state = "critical"
        elif risk_score >= 30:
            severity = "medium"
            priority = "high"
            recommended_state = "warning"
        else:
            severity = "low"
            priority = "low"
            recommended_state = "normal"

        # 8. Determine whether an incident is required (CORREGIDO SEGÚN ARQUITECTURA)
        incident_required = (
            recommended_state != "normal"
        )

        # 9. Identify possible incident type
        if variables:
            incident_type = (
                "possible_" + "_and_".join(variables) + "_failure"
            )
        elif pattern_detected:
            incident_type = "shared_anomalous_behavior"
        else:
            incident_type = "historical_risk_detected"

        # 10. Build result
        result = {
            "tenant_id": tenant_id,
            "machine_id": machine_id,

            "incident_required": incident_required,
            "incident_type": incident_type,

            "severity": severity,
            "priority": priority,
            "risk_score": risk_score,

            "recommended_state": recommended_state,

            "analysis": {
                "anomaly_score": round(anomaly_score, 2),
                "pattern_detected": pattern_detected,
                "pattern_score": round(pattern_score, 2),
                "historical_risk_score": historical_score,
                "anomalous_variables": variables
            }
        }

        # Return a direct JSON object for Step Functions
        return result

    except Exception as e:
        print(f"Error evaluating incident: {str(e)}")
        raise
