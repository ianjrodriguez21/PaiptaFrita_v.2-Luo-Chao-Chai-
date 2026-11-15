
from pathlib import Path

import joblib
import pandas as pd

from sklearn.ensemble import IsolationForest
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "data" / "telemetria_entrenamiento.csv"
MODEL_DIR = BASE_DIR / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

FEATURES = [
    "temperature",
    "vibration",
    "pressure",
]

# 1. Cargar los datos
datos = pd.read_csv(DATA_PATH)

# 2. Separar datos normales y anormales
normales = datos[datos["anomaly"] == 0]
anormales = datos[datos["anomaly"] == 1]

# 3. Reservar datos normales para evaluación
train_normal, test_normal = train_test_split(
    normales,
    test_size=0.2,
    random_state=42,
)

# 4. Crear modelo Isolation Forest
modelo = Pipeline([
    ("scaler", StandardScaler()),
    ("isolation_forest", IsolationForest(
        n_estimators=200,
        contamination=0.02,
        random_state=42,
        n_jobs=-1,
    )),
])

# 5. Entrenar únicamente con datos normales
modelo.fit(train_normal[FEATURES])

print("Modelo entrenado correctamente")

# 6. Evaluar con datos que no vio durante el entrenamiento
test = pd.concat(
    [test_normal, anormales],
    ignore_index=True,
)

# Isolation Forest devuelve:
#  1 = normal
# -1 = anomalia
predicciones = modelo.predict(test[FEATURES])

# Convertir a etiquetas 0 y 1
y_pred = (predicciones == -1).astype(int)
y_true = test["anomaly"].to_numpy()

print("\nResultados de evaluación:")
print(classification_report(
    y_true,
    y_pred,
    target_names=["Normal", "Anomalia"],
    zero_division=0,
))

print("Matriz de confusion:")
print(confusion_matrix(y_true, y_pred))

# 7. Guardar el modelo y el escalador juntos
MODEL_PATH = MODEL_DIR / "anomaly_model.joblib"
joblib.dump(modelo, MODEL_PATH)

print(f"\nModelo guardado en: {MODEL_PATH}")

# 8. Probar una lectura nueva
lectura = pd.DataFrame([{
    "temperature": 110.0,
    "vibration": 7.8,
    "pressure": 55.0,
}])

resultado = modelo.predict(lectura)[0]
score = modelo.decision_function(lectura)[0]

print("\nPrueba con lectura nueva:")
print(lectura.to_string(index=False))
print("Prediccion:", "ANOMALIA" if resultado == -1 else "NORMAL")
print("Score de decision:", round(float(score), 4))
