import json


def unwrap_result(value):
    """
    Permite recibir tanto un diccionario estándar de Step Functions
    como una respuesta Lambda proxy con statusCode y body.
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
    """Convierte un puntaje de escala 0-1 o 0-100 a escala uniforme 0-100."""
    if value is None:
        return 0.0

    score = float(value)
    if 0 <= score <= 1:
        score *= 100

    return max(0.0, min(score, 100.0))


def get_anomaly_score(anomaly_result):
    anomalies = anomaly_result.get("anomalies", [])
    if not anomalies:
        return 0.0, []

    scores = []
    variables = []

    for anomaly in anomalies:
        score = normalize_score(anomaly.get("anomaly_score", 0))
        scores.append(score)

        variable = anomaly.get("variable")
        if variable:
            variables.append(variable)

    # Severidad promedio de las anomalías detectadas
    average_score = sum(scores) / len(scores)

    # Múltiples anomalías simultáneas incrementan el riesgo
    if len(anomalies) >= 3:
        average_score = min(average_score + 10, 100.0)

    return average_score, variables


def infer_incident_type(variables, pattern_detected):
    """[CORREGIDO]: Mapea combinaciones de variables a tipos de falla claros como en el PDF."""
    var_set = set(variables)
    if {"temperature", "vibration"}.issubset(var_set):
        return "possible_bearing_failure"
    if "pressure" in var_set:
        return "possible_hydraulic_pressure_failure"
    if "fuel_consumption" in var_set:
        return "abnormal_fuel_consumption"
    if variables:
        return "possible_" + "_and_".join(variables) + "_failure"
    if pattern_detected:
        return "shared_anomalous_behavior"
    return "historical_risk_detected"


def lambda_handler(event, context):
    try:
        # [CORREGIDO]: Soporta tanto estructura anidada (anomaly_result/pattern_result/historical_result)
        # como llaves planas en la raíz de 'event' si Step Functions encadena sin ResultPath
        anomaly_result = unwrap_result(event.get("anomaly_result")) or {
            "anomalies": event.get("anomalies", []),
            "anomaly_detected": event.get("anomaly_detected", False)
        }
        pattern_result = unwrap_result(event.get("pattern_result")) or {
            "pattern_detected": event.get("pattern_detected", False),
            "confidence": event.get("confidence", 0.60)
        }
        historical_result = unwrap_result(event.get("historical_result")) or {
            "historical_risk_score": event.get("historical_risk_score", 0),
            "recurrence_detected": event.get("recurrence_detected", False),
            "findings": event.get("findings", [])
        }

        # Identificar máquina y tenant
        tenant_id = event.get("tenant_id") or anomaly_result.get("tenant_id")
        machine_id = event.get("machine_id") or anomaly_result.get("machine_id")

        if not tenant_id or not machine_id:
            raise ValueError("tenant_id and machine_id are required")

        # Calcular puntaje de anomalía actual
        anomaly_score, variables = get_anomaly_score(anomaly_result)

        # Evaluar patrones compartidos
        pattern_detected = bool(pattern_result.get("pattern_detected", False))
        if pattern_detected:
            raw_conf = pattern_result.get("confidence")
            confidence = normalize_score(raw_conf if raw_conf not in (None, 0, 0.0) else 0.60)
            pattern_score = confidence
        else:
            pattern_score = 0.0

        # Obtener riesgo histórico
        historical_score = normalize_score(
            historical_result.get("historical_risk_score", 0)
        )

        # Calcular riesgo combinado (50% anomalía, 20% patrón compartido, 30% histórico)
        risk_score = (
            anomaly_score * 0.50
            + pattern_score * 0.20
            + historical_score * 0.30
        )
        risk_score = round(max(0.0, min(risk_score, 100.0)))

        # Clasificar incidente
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

        # Determinar si requiere crear incidente en Step Functions
        incident_required = recommended_state != "normal"

        # Identificar tipo de incidente
        incident_type = infer_incident_type(variables, pattern_detected)

        evaluation_result = {
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
                "historical_risk_score": round(historical_score, 2),
                "anomalous_variables": variables
            }
        }

        # [CORREGIDO]: Incluye evaluation_result, historical_result y machine_data
        # para alimentar directamente a RecomendarMantenimiento.py
        salida = {
            **evaluation_result,
            "evaluation_result": evaluation_result,
            "historical_result": historical_result,
            "machine_data": event.get("machine_data", {})
        }

        return salida

    except Exception as e:
        print(f"Error evaluating incident: {str(e)}")
        raise
