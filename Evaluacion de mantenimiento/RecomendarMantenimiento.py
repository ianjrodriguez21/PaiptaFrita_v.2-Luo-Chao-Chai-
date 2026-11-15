
def lambda_handler(event, context):
    try:
        tenant_id = event.get("tenant_id")
        machine_id = event.get("machine_id")

        evaluation = event.get("evaluation_result", {})
        machine = event.get("machine_data", {})
        historical = event.get("historical_result", {})
        policy = event.get("maintenance_policy", {})

        if not tenant_id or not machine_id:
            raise ValueError("tenant_id and machine_id are required")

        risk_score = float(evaluation.get("risk_score", 0))
        state = evaluation.get("recommended_state", "normal")

        historical_risk = float(
            historical.get("historical_risk_score", 0)
        )

        recurrence = historical.get("recurrence_detected", False)

        hours_since_maintenance = float(
            machine.get("operating_hours_since_last_maintenance", 0)
        )

        preventive_interval = float(
            policy.get("preventive_interval_hours", 250)
        )

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

        # 2. Riesgo warning con recurrencia
        elif state == "warning" or risk_score >= 30:
            recommendation_required = True

            if recurrence or historical_risk >= 70:
                maintenance_type = "predictive"
                priority = "high"
                recommended_time = "within_24_hours"
                reason = (
                    "Recurring anomalies or high historical risk detected."
                )
            else:
                maintenance_type = "preventive"
                priority = "medium"
                recommended_time = "within_7_days"
                reason = (
                    "Abnormal conditions detected. Preventive maintenance "
                    "is recommended."
                )

        # 3. Riesgo normal: revisar intervalo de mantenimiento
        elif hours_since_maintenance >= preventive_interval:
            recommendation_required = True
            maintenance_type = "preventive"
            priority = "medium"
            recommended_time = "scheduled"
            reason = "Preventive maintenance interval has been reached."

        # 4. Construir respuesta
        if recommendation_required:
            recommendation = {
                "maintenance_type": maintenance_type,
                "priority": priority,
                "recommended_time": recommended_time,
                "reason": reason
            }
        else:
            recommendation = None

        result = {
            "tenant_id": tenant_id,
            "machine_id": machine_id,
            "machine_type": machine_type,
            "recommendation_required": recommendation_required,
            "recommendation": recommendation,
            "maintenance_context": {
                "risk_score": risk_score,
                "historical_risk_score": historical_risk,
                "recurrence_detected": recurrence,
                "operating_hours_since_last_maintenance":
                    hours_since_maintenance,
                "preventive_interval_hours": preventive_interval
            }
        }

        return result

    except Exception as e:
        print(f"Error recommending maintenance: {str(e)}")
        raise
