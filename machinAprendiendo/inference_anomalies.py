import pandas as pd
import json
from pathlib import Path

import joblib
import numpy as np


def model_fn(model_dir):
    """Cargar el modelo entrenado."""
    ruta = Path(model_dir) / "anomaly_model.joblib"
    return joblib.load(ruta)


def input_fn(request_body, content_type):
    """Recibir la solicitud JSON de SageMaker."""
    if content_type != "application/json":
        raise ValueError("Solo se acepta application/json")

    data = json.loads(request_body)

    # Contrato de nuestra Lambda DetectarAnomalias:
    # {"features": [temperatura, vibracion, presion]}
    features = data["features"]

    if not isinstance(features, list) or len(features) != 3:
        raise ValueError("Se requieren 3 caracteristicas")

    valores = np.asarray(features, dtype=float)

    if not np.isfinite(valores).all():
        raise ValueError("Caracteristicas no validas")

    return pd.DataFrame(
        [valores],
    columns=[
            "temperature",
            "vibration",
            "pressure",
        ],
    )


def predict_fn(input_data, model):
    """Ejecutar el modelo Isolation Forest."""
    prediction = int(model.predict(input_data)[0])
    score = float(model.decision_function(input_data)[0])

    return {
        "prediction": prediction,
        "anomaly_score": round(score, 6),
    }


def output_fn(prediction, accept):
    """Preparar la respuesta para Lambda."""
    if accept != "application/json":
        raise ValueError("Solo se devuelve application/json")

    return json.dumps(prediction)
