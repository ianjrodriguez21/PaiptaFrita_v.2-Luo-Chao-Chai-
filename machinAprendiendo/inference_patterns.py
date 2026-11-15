import pandas as pd
import json
from pathlib import Path

import joblib
import numpy as np


def model_fn(model_dir):
    """Cargar el modelo K-Means."""
    ruta = Path(model_dir) / "pattern_model.joblib"
    return joblib.load(ruta)


def input_fn(request_body, content_type):
    """Recibir las alertas enviadas por DetectarPatrones."""
    if content_type != "application/json":
        raise ValueError("Solo se acepta application/json")

    data = json.loads(request_body)
    instances = data.get("instances")

    if not isinstance(instances, list):
        raise ValueError("instances debe ser una lista")

    for instance in instances:
        if not isinstance(instance, dict):
            raise ValueError("Instancia invalida")

        if not instance.get("alert_id"):
            raise ValueError("Falta alert_id")

        if not instance.get("machine_id"):
            raise ValueError("Falta machine_id")

        features = instance.get("features")

        if not isinstance(features, list) or len(features) != 4:
            raise ValueError("Se requieren 4 caracteristicas")

        values = np.asarray(features, dtype=float)

        if not np.isfinite(values).all():
            raise ValueError("Caracteristicas no validas")

    return instances


def predict_fn(instances, model):
    """Asignar cada alerta a un cluster."""
    if not instances:
        return {"assignments": []}

    
    datos = pd.DataFrame(
        [item["features"] for item in instances],
        columns=[
            "temperature",
            "vibration",
            "pressure",
            "fuel",
        ],
        dtype=float,
    )

    clusters = model.predict(datos)

    assignments = []

    for item, cluster in zip(instances, clusters):
        assignments.append({
            "alert_id": item["alert_id"],
            "machine_id": item["machine_id"],
            "cluster_id": int(cluster),
        })

    return {"assignments": assignments}


def output_fn(prediction, accept):
    """Devolver JSON compatible con DetectarPatrones."""
    if accept != "application/json":
        raise ValueError("Solo se devuelve application/json")

    return json.dumps(prediction)
