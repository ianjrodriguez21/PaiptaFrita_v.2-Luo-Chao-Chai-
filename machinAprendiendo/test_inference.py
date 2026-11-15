
import json
from pathlib import Path

import inference_anomalies
import inference_patterns

BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "models"


def probar_anomalias():
    print("\n=== PRUEBA DE ANOMALIAS ===")

    modelo = inference_anomalies.model_fn(MODEL_DIR)

    solicitud = json.dumps({
        "features": [110.0, 7.8, 55.0]
    })

    datos = inference_anomalies.input_fn(
        solicitud, "application/json"
    )

    prediccion = inference_anomalies.predict_fn(
        datos, modelo
    )

    respuesta = inference_anomalies.output_fn(
        prediccion, "application/json"
    )

    print(respuesta)

    assert prediccion["prediction"] in (-1, 1)
    assert isinstance(prediccion["anomaly_score"], float)

    print("Contrato de anomalias: OK")


def probar_patrones():
    print("\n=== PRUEBA DE PATRONES ===")

    modelo = inference_patterns.model_fn(MODEL_DIR)

    solicitud = json.dumps({
        "instances": [
            {
                "alert_id": "ALT-001",
                "machine_id": "MAQ-001",
                "timestamp": "2026-11-15T10:00:00Z",
                "features": [110.0, 7.8, 55.0, 18.0]
            },
            {
                "alert_id": "ALT-002",
                "machine_id": "MAQ-002",
                "timestamp": "2026-11-15T10:01:00Z",
                "features": [108.0, 7.5, 57.0, 17.5]
            }
        ]
    })

    datos = inference_patterns.input_fn(
        solicitud, "application/json"
    )

    prediccion = inference_patterns.predict_fn(
        datos, modelo
    )

    respuesta = inference_patterns.output_fn(
        prediccion, "application/json"
    )

    print(respuesta)

    assert len(prediccion["assignments"]) == 2
    assert all(
        isinstance(item["cluster_id"], int)
        for item in prediccion["assignments"]
    )

    print("Contrato de patrones: OK")


if __name__ == "__main__":
    probar_anomalias()
    probar_patrones()

    print("\nAMBAS PRUEBAS FINALIZARON CORRECTAMENTE")
