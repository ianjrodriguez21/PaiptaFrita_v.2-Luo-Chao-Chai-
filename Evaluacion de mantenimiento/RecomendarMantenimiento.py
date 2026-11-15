def get_recommended_action(anomalous_variables, maintenance_type):
    """Genera una acción recomendada según las variables anómalas detectadas."""
    if not anomalous_variables:
        if maintenance_type == "preventive":
            return "Perform scheduled inspection, lubrication, and filter check"
        return "No immediate action required"

    actions = []
    if "temperature" in anomalous_variables:
        actions.append("inspect cooling and lubrication system")
    if "vibration" in anomalous_variables:
        actions.append("inspect main bearings, alignment, and mounts")
    if "pressure" in anomalous_variables:
        actions.append("check hydraulic lines, valves, and seals")
    if "fuel_consumption" in anomalous_variables:
        actions.append("inspect fuel injectors and engine efficiency")

    if not actions:
        return "Perform general diagnostic inspection on flagged components"

    formatted = ", ".join(actions)
    return formatted[0].upper() + formatted[1:]


def lambda_handler(event, context):
    try:
        tenant_id = event.get("tenant_id")
        machine_id = event.get("machine_id")

        if not tenant_id or not machine_id:
            raise ValueError("tenant_id and machine_id are required")

        # Soportar tanto objetos anidados (ResultPath) como datos en la raíz del evento
        evaluation = event.get("evaluation_result", event)
        machine = event.get("machine_data", event)
        historical = event.get("historical_result", event)
        policy = event.get("maintenance_policy", {})

        risk_score = float(evaluation.get("risk_score", 0))
        state = evaluation.get("recommended_state", "normal")
        anomalous_variables = evaluation.get("analysis", {}).get("anomalous_variables", [])

        historical_risk = float(historical.get("historical_risk_score", 0))
        recurrence = historical.get("recurrence_detected", False)

        # Fallback al campo 'operating_hours' del JSON de telemetría (PDF pág. 2)
        hours_since_maintenance = float(
            machine.get(
                "operating_hours_since_last_maintenance",
                machine.get("operating_hours", 0)
            )
        )

        preventive_interval = float(policy.get("preventive_interval_hours", 250))
        machine_type = machine.get("machine_type", "unknown")

        # Valores iniciales
        recommendation_required = False
        maintenance_type = None
        priority = "low"
        recommended_time = None
        reason = "No maintenance required at this moment"

        # 1. Riesgo crítico
        if state == "critical" or risk_score >= 70:
            recommendation_required = True
            maintenance_type = "corrective"
            priority = "urgent"
            recommended_time = "immediately"
            reason = "Critical risk detected. Immediate maintenance is required."

        # 2. Riesgo warning con o sin recurrencia
        elif state == "warning" or risk_score >= 30:
            recommendation_required = True

            if recurrence or historical_risk >= 70:
                maintenance_type = "predictive"
                priority = "high"
                recommended_time = "within_24_hours"
                reason = "Recurring anomalies or high historical risk detected."
            else:
                maintenance_type = "preventive"
                priority = "medium"
                recommended_time = "within_7_days"
                reason = "Abnormal conditions detected. Preventive maintenance is recommended."

        # 3. Riesgo normal: revisar intervalo de mantenimiento
        elif hours_since_maintenance >= preventive_interval:
            recommendation_required = True
            maintenance_type = "preventive"
            priority = "medium"
            recommended_time = "scheduled"
            reason = "Preventive maintenance interval has been reached."

        recommended_action = get_recommended_action(anomalous_variables, maintenance_type)
        maintenance_score = round(max(risk_score, historical_risk))

        # 4. Construir respuesta alineada con el PDF (pág. 11) y Step Functions
        recommendation = None
        if recommendation_required:
            recommendation = {
                "maintenance_type": maintenance_type,
                "priority": priority,
                "recommended_time": recommended_time,
                "recommended_action": recommended_action,
                "reason": reason
            }

        result = {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "machine_type": machine_type,

            # Propagamos recommended_state para el rombo de decisión de Step Functions
            "recommended_state": state,

            # Campos del contrato del PDF (pág. 11)
            "maintenance_recommended": recommendation_required,
            "maintenance_type": maintenance_type,
            "maintenance_score": maintenance_score,
            "priority": priority,
            "recommended_action": recommended_action,
            "reason": reason,

            # Tu estructura original conservada
            "recommendation_required": recommendation_required,
            "recommendation": recommendation,
            "maintenance_context": {
                "risk_score": risk_score,
                "historical_risk_score": historical_risk,
                "recurrence_detected": recurrence,
                "operating_hours_since_last_maintenance": hours_since_maintenance,
                "preventive_interval_hours": preventive_interval
            }
        }

        return result

    except Exception as e:
        print(f"Error recommending maintenance: {str(e)}")
        raise
