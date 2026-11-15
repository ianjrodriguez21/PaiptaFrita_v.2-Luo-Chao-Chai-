"""
Lambda: RecomendarMantenimiento

Entrada:
    machine_data
    evaluation_result
    historical_result
    maintenance_policy (opcional)

Salida:
    maintenance_result (JSON directo)

No escribe en DynamoDB.
No crea incidentes.
No asigna técnicos.

La ejecución de mantenimiento se decide
posteriormente en Step Functions.
"""

import logging
import math

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def optional_number(value, name):
    """Convierte un número opcional y lo valida."""

    if value is None:
        return None

    if isinstance(value, bool):
        raise ValueError(
            f"{name} no puede ser booleano"
        )

    try:
        number = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"{name} debe ser numerico"
        ) from exc

    if not math.isfinite(number) or number < 0:
        raise ValueError(
            f"{name} debe ser positivo o cero"
        )

    return number


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

        machine = event.get("machine_data")
        evaluation = event.get("evaluation_result")
        historical = event.get("historical_result")

        policy = event.get(
            "maintenance_policy", {}
        )

        if not all(
            isinstance(value, dict)
            for value in [
                machine,
                evaluation,
                historical,
                policy
            ]
        ):
            raise ValueError(
                "Contexto de mantenimiento incompleto"
            )

        # 2. Verificar identidad de la evaluacion
        if (
            evaluation.get("tenant_id") != tenant_id
            or evaluation.get("machine_id") != machine_id
        ):
            raise ValueError(
                "evaluation_result no corresponde a la maquina"
            )

        # 3. Recuperar riesgo
        risk_score = optional_number(
            evaluation.get("risk_score"),
            "risk_score"
        )

        historical_risk = optional_number(
            historical.get("historical_risk_score"),
            "historical_risk_score"
        )

        if risk_score is None or historical_risk is None:
            raise ValueError(
                "Faltan los puntajes de riesgo"
            )

        if risk_score > 100 or historical_risk > 100:
            raise ValueError(
                "Los puntajes deben estar entre 0 y 100"
            )

        state = evaluation.get(
            "recommended_state"
        )

        if state not in (
            "normal", "warning", "critical"
        ):
            raise ValueError(
                "recommended_state invalido"
            )

        recurrence = bool(
            historical.get(
                "recurrence_detected", False
            )
        )

        # 4. Datos operativos
        machine_type = machine.get(
            "machine_type", "unknown"
        )

        operating_hours = optional_number(
            machine.get("operating_hours"),
            "operating_hours"
        )

        hours_since = optional_number(
            machine.get(
                "operating_hours_since_last_maintenance"
            ),
            "operating_hours_since_last_maintenance"
        )

        preventive_interval = optional_number(
            policy.get(
                "preventive_interval_hours", 250
            ),
            "preventive_interval_hours"
        )

        if preventive_interval is None or preventive_interval <= 0:
            raise ValueError(
                "preventive_interval_hours debe ser mayor a cero"
            )

        # Si no sabemos cuándo fue el último
        # mantenimiento, no asumimos que todas
        # las horas de operación son recientes.
        preventive_due = (
            hours_since is not None
            and hours_since >= preventive_interval
        )

        # 5. Valores por defecto
        maintenance_recommended = False
        maintenance_type = None
        priority = "low"
        recommended_time = None
        recommended_action = (
            "Continue normal operation and monitoring."
        )
        reason = (
            "No maintenance required at this moment."
        )

        # 6. Clasificar mantenimiento

        # Caso CRITICAL
        if state == "critical":

            maintenance_recommended = True
            maintenance_type = "corrective"
            priority = "urgent"
            recommended_time = "immediately"

            recommended_action = (
                "Stop or isolate equipment safely "
                "and request immediate technical inspection."
            )

            reason = (
                "Critical risk detected from "
                "anomalies and historical analysis."
            )

        # Caso WARNING
        elif state == "warning":

            maintenance_recommended = True

            if recurrence or historical_risk >= 70:

                maintenance_type = "predictive"
                priority = "high"
                recommended_time = "within_24_hours"

                recommended_action = (
                    "Schedule a specialized inspection "
                    "of the affected components."
                )

                reason = (
                    "Recurring anomalies or elevated "
                    "historical risk detected."
                )

            else:
                maintenance_type = "preventive"
                priority = "medium"
                recommended_time = "within_7_days"

                recommended_action = (
                    "Schedule preventive inspection "
                    "and diagnostic checks."
                )

                reason = (
                    "Warning-level operating conditions "
                    "were detected."
                )

        # Caso NORMAL con mantenimiento vencido
        elif preventive_due:

            maintenance_recommended = True
            maintenance_type = "preventive"
            priority = "medium"
            recommended_time = "scheduled"

            recommended_action = (
                "Schedule preventive maintenance "
                "based on operating hours."
            )

            reason = (
                "Preventive maintenance interval "
                "has been reached."
            )

        # 7. Puntaje de mantenimiento
        maintenance_score = round(
            max(risk_score, historical_risk)
        )

        # 8. Recomendacion detallada
        if maintenance_recommended:
            recommendation = {
                "maintenance_type": maintenance_type,
                "priority": priority,
                "recommended_time": recommended_time,
                "recommended_action": recommended_action,
                "reason": reason
            }
        else:
            recommendation = None

        # 9. Decidir accion del flujo
        # El riesgo determina si necesita incidente.
        incident_required = evaluation.get(
            "incident_required"
        )

        expected_incident = (
            state != "normal"
        )

        if (
            type(incident_required) is not bool
            or incident_required != expected_incident
        ):
            raise ValueError(
                "incident_required no coincide con el estado"
            )

        if state == "critical":
            next_action = "urgent_intervention"

        elif state == "warning":
            next_action = "schedule_maintenance"

        elif maintenance_recommended:
            next_action = "schedule_preventive"

        else:
            next_action = "normal_operation"

        # 10. Salida para Step Functions
        result = {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "timestamp": timestamp,
            "machine_type": machine_type,
            "recommended_state": state,
            "incident_required": incident_required,
            "maintenance_recommended": maintenance_recommended,
            "maintenance_type": maintenance_type,
            "maintenance_score": maintenance_score,
            "risk_score": risk_score,
            "severity": evaluation.get("severity"),
            "incident_type": evaluation.get(
                "incident_type"
            ),
            "priority": priority,
            "recommended_time": recommended_time,
            "recommended_action": recommended_action,
            "reason": reason,
            "next_action": next_action,
            "recommendation": recommendation,
            "maintenance_context": {
                "risk_score": risk_score,
                "historical_risk_score": historical_risk,
                "recurrence_detected": recurrence,
                "operating_hours": operating_hours,
                "operating_hours_since_last_maintenance": (
                    hours_since
                ),
                "preventive_interval_hours": (
                    preventive_interval
                ),
                "preventive_due": preventive_due
            }
        }

        logger.info(
            "Mantenimiento %s: %s",
            machine_id,
            next_action
        )

        return result

    except Exception:
        logger.exception(
            "Error en RecomendarMantenimiento"
        )
        raise
