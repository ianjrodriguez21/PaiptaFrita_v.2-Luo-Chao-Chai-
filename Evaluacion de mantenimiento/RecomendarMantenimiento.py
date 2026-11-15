def lambda_handler(event, context):
    try:
        tenant_id = event.get("tenant_id")
        machine_id = event.get("machine_id")

        if not tenant_id or not machine_id:
            raise ValueError("tenant_id and machine_id are required")

        # [CORREGIDO]: Soporta tanto objetos anidados como llaves planas en 'event'
        evaluation = event.get("evaluation_result") or event
        machine = event.get("machine_data") or event
        historical = event.get("historical_result") or event
        policy = event.get("maintenance_policy") or {}

        risk_score = float(evaluation.get("risk_score", 0))
        state = evaluation.get("recommended_state", "normal")
        incident_required = evaluation.get("incident_required", state != "normal")

        historical_risk = float(historical.get("historical_risk_score", 0))
        recurrence = bool(historical.get("recurrence_detected", False))

        # [CORREGIDO]: El JSON de entrada del PDF envía 'operating_hours', no 'operating_hours_since_last_maintenance'.
        # Ahora soporta ambos campos y evalúa ciclos de mantenimiento preventivo correctamente.
        raw_hours_since = machine.get("operating_hours_since_last_maintenance")
        total_operating_hours = float(machine.get("operating_hours", 0))
        preventive_interval = float(policy.get("preventive_interval_hours", 250))

        if raw_hours_since is not None:
            hours_since_maintenance = float(raw_hours_since)
        else:
            hours_since_maintenance = total_operating_hours

        machine_type = machine.get("machine_type", "unknown")

        # Valores iniciales
        recommendation_required = False
        maintenance_type = None
        priority = "low"
        recommended_time = None
        recommended_action = "Continue normal operation and standard monitoring."
        reason = "No maintenance required at this moment"

        # 1. Riesgo crítico
        if state == "critical" or risk_score >= 70:
            recommendation_required = True
            maintenance_type = "corrective"
            priority = "urgent"
            recommended_time = "immediately"
            recommended_action = "Stop equipment immediately and dispatch specialized technician for inspection."
            reason = "Critical risk detected. Immediate maintenance is required."

        # 2. Riesgo warning (preventivo o predictivo según recurrencia)
        elif state == "warning" or risk_score >= 30:
            recommendation_required = True

            if recurrence or historical_risk >= 70:
                maintenance_type = "predictive"
                priority = "high"
                recommended_time = "within_24_hours"
                recommended_action = "Inspect affected components (bearings, hydraulics, lubrication) within 24 hours."
                reason = "Recurring anomalies or high historical risk detected."
            else:
                maintenance_type = "preventive"
                priority = "medium"
                recommended_time = "within_7_days"
                recommended_action = "Schedule preventive inspection and diagnostic check within 7 days."
                reason = "Abnormal conditions detected. Preventive maintenance is recommended."

        # 3. Riesgo normal: revisar intervalo de mantenimiento preventivo por horas de operación
        elif hours_since_maintenance >= preventive_interval:
            recommendation_required = True
            maintenance_type = "preventive"
            priority = "medium"
            recommended_time = "scheduled"
            recommended_action = "Perform scheduled preventive maintenance according to operating hours interval."
            reason = "Preventive maintenance interval has been reached."

        # Construir objeto detallado de recomendación
        if recommendation_required:
            recommendation = {
                "maintenance_type": maintenance_type,
                "priority": priority,
                "recommended_time": recommended_time,
                "recommended_action": recommended_action,
                "reason": reason
            }
        else:
            recommendation = None

        # [CORREGIDO]: Salida unificada que cumple tanto con el esquema del PDF
        # (maintenance_recommended, maintenance_score, recommended_action) como con el rombo de Step Functions
        # (recommended_state, incident_required, recommendation)
        maintenance_score = round(max(risk_score, historical_risk))

        result = {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "machine_type": machine_type,
            "recommended_state": state,
            "incident_required": incident_required,
            "risk_score": risk_score,
            "severity": evaluation.get("severity", "low"),
            "incident_type": evaluation.get("incident_type"),
            "recommendation_required": recommendation_required,
            "maintenance_recommended": recommendation_required,
            "maintenance_type": maintenance_type,
            "maintenance_score": maintenance_score,
            "priority": priority,
            "recommended_time": recommended_time,
            "recommended_action": recommended_action,
            "reason": reason,
            "recommendation": recommendation,
            "maintenance_context": {
                "risk_score": risk_score,
                "historical_risk_score": historical_risk,
                "recurrence_detected": recurrence,
                "operating_hours": total_operating_hours,
                "operating_hours_since_last_maintenance": hours_since_maintenance,
                "preventive_interval_hours": preventive_interval
            }
        }

        return result

    except Exception as e:
        print(f"Error recommending maintenance: {str(e)}")
        raise
