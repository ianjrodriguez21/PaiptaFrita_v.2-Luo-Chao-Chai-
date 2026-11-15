
"""
Lambda: EvaluarIncidente

Entrada:
    anomaly_result
    pattern_result
    historical_result

Salida:
    evaluation_result (JSON directo)

No utiliza DynamoDB.
No crea incidentes.
Solo calcula el riesgo y clasifica.
"""

import logging
import math

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def validate_score(value, name, scale=100):
    """
    Valida un puntaje dentro de la escala indicada.
    """
    if isinstance(value, bool):
        raise ValueError(f"{name} invalido")

    try:
        score = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"{name} debe ser numerico"
        ) from exc

    if not math.isfinite(score):
        raise ValueError(
            f"{name} debe ser finito"
        )

    if not 0 <= score <= scale:
        raise ValueError(
            f"{name} fuera de rango 0-{scale}"
        )

    return score


def calculate_anomaly_score(anomaly_result):
    """
    Convierte scores de anomalía a escala 0-100.

    Escala provisional:
    anomaly_score 0-1 -> 0-100.

    IMPORTANTE: esto supone que el score del
    modelo se ha calibrado a esa escala.
    """

    if not anomaly_result.get(
        "anomaly_detected", False
    ):
        return 0.0, []

    anomalies = anomaly_result.get(
        "anomalies", []
    )

    if not isinstance(anomalies, list):
        raise ValueError(
            "anomalies debe ser una lista"
        )

    variables = []

    for anomaly in anomalies:
        variable = anomaly.get("variable")

        if variable and variable not in variables:
            variables.append(variable)

    # Preferir el puntaje global calculado
    # por DetectarAnomalias.
    raw_score = anomaly_result.get(
        "anomaly_score"
    )

    if raw_score is None:
        if not anomalies:
            raise ValueError(
                "Falta anomaly_score en anomalia detectada"
            )

        raw_score = max(
            validate_score(
                anomaly.get("anomaly_score"),
                "anomaly_score",
                1
            )
            for anomaly in anomalies
        )

    score = validate_score(
        raw_score,
        "anomaly_score",
        1
    ) * 100

    # Varias variables anomalas aumentan el riesgo
    if len(variables) >= 3:
        score = min(
            100.0,
            score + 10
        )

    return round(score, 2), variables


def infer_incident_type(
    variables, pattern_detected,
    historical_risk
):
    """Identifica el tipo probable de falla."""

    variable_set = set(variables)

    if {
        "temperature",
        "vibration"
    }.issubset(variable_set):
        return "possible_bearing_failure"

    if "pressure" in variable_set:
        return "possible_hydraulic_pressure_failure"

    if "fuel_consumption" in variable_set:
        return "abnormal_fuel_consumption"

    if "vibration" in variable_set:
        return "abnormal_vibration"

    if "temperature" in variable_set:
        return "abnormal_temperature"

    if pattern_detected:
        return "shared_anomalous_behavior"

    if historical_risk > 0:
        return "historical_risk_detected"

    return None


def lambda_handler(event, context):
    try:
        # 1. Recuperar contexto
        tenant_id = event.get("tenant_id")
        machine_id = event.get("machine_id")
        timestamp = event.get("timestamp")

        if not tenant_id or not machine_id or not timestamp:
            raise ValueError(
                "tenant_id, machine_id y timestamp "
                "son obligatorios"
            )

        anomaly_result = event.get(
            "anomaly_result"
        )

        pattern_result = event.get(
            "pattern_result"
        )

        historical_result = event.get(
            "historical_result"
        )

        if not all(
            isinstance(value, dict)
            for value in [
                anomaly_result,
                pattern_result,
                historical_result
            ]
        ):
            raise ValueError(
                "Faltan resultados de analisis previos"
            )

        # 2. Riesgo de anomalías
        anomaly_score, variables = (
            calculate_anomaly_score(
                anomaly_result
            )
        )

        # 3. Riesgo de patrones
        pattern_detected = (
            pattern_result.get(
                "pattern_detected", False
            )
        )

        if pattern_detected:
            pattern_score = validate_score(
                pattern_result.get("confidence"),
                "confidence",
                1
            ) * 100
        else:
            pattern_score = 0.0

        # 4. Riesgo histórico
        historical_score = validate_score(
            historical_result.get(
                "historical_risk_score"
            ),
            "historical_risk_score",
            100
        )

        recurrence_detected = bool(
            historical_result.get(
                "recurrence_detected", False
            )
        )

        # 5. Riesgo total ponderado
        risk_score = (
            anomaly_score * 0.50
            + pattern_score * 0.20
            + historical_score * 0.30
        )

        risk_score = round(
            min(100, max(0, risk_score))
        )

        # 6. Clasificación
        if risk_score >= 70:
            recommended_state = "critical"
            severity = "critical"
            priority = "urgent"

        elif risk_score >= 30:
            recommended_state = "warning"
            severity = "medium"
            priority = "high"

        else:
            recommended_state = "normal"
            severity = "low"
            priority = "low"

        incident_required = (
            recommended_state != "normal"
        )

        # 7. Tipo de incidente
        incident_type = infer_incident_type(
            variables,
            pattern_detected,
            historical_score
        )

        result = {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "timestamp": timestamp,
            "incident_required": incident_required,
            "incident_type": incident_type,
            "severity": severity,
            "priority": priority,
            "risk_score": risk_score,
            "recommended_state": recommended_state,
            "analysis": {
                "anomaly_score": anomaly_score,
                "pattern_detected": pattern_detected,
                "pattern_score": round(
                    pattern_score, 2
                ),
                "historical_risk_score": (
                    historical_score
                ),
                "recurrence_detected": (
                    recurrence_detected
                ),
                "anomalous_variables": variables
            }
        }

        logger.info(
            "Maquina %s evaluada: %s (%s)",
            machine_id,
            recommended_state,
            risk_score
        )

        return result

    except Exception:
        logger.exception(
            "Error en EvaluarIncidente"
        )
        raise
