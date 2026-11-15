
from pathlib import Path

import joblib
import pandas as pd

from sklearn.cluster import KMeans
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import silhouette_score
from sklearn.model_selection import train_test_split

BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "data" / "telemetria_entrenamiento.csv"
MODEL_DIR = BASE_DIR / "models"

MODEL_DIR.mkdir(parents=True, exist_ok=True)

FEATURES = [
    "temperature",
    "vibration",
    "pressure",
    "fuel",
]

# 1. Cargar el dataset
datos = pd.read_csv(DATA_PATH)

# 2. Usar lecturas anómalas para aprender patrones
anomalias = datos[datos["anomaly"] == 1].copy()

X = anomalias[FEATURES]

# 3. Separar datos para una evaluación orientativa
X_train, X_test = train_test_split(
    X,
    test_size=0.2,
    random_state=42,
)

# 4. Crear modelo de agrupamiento
modelo = Pipeline([
    ("scaler", StandardScaler()),
    ("kmeans", KMeans(
        n_clusters=3,
        random_state=42,
        n_init=10,
    )),
])

# 5. Entrenar
modelo.fit(X_train)

print("Modelo K-Means entrenado correctamente")

# 6. Evaluar agrupamiento
clusters = modelo.predict(X_test)
X_test_scaled = modelo.named_steps["scaler"].transform(X_test)

if len(set(clusters)) > 1:
    silhouette = silhouette_score(
        X_test_scaled,
        clusters,
    )
    print(f"Silhouette Score: {silhouette:.4f}")
else:
    print("No se puede calcular Silhouette Score")

# 7. Mostrar distribución de clusters
resultados = X_test.copy()
resultados["cluster"] = clusters

print("\nDistribucion de clusters:")
print(resultados["cluster"].value_counts().sort_index())

print("\nPromedios por cluster:")
print(resultados.groupby("cluster")[FEATURES].mean().round(2))

# 8. Guardar modelo y escalador
MODEL_PATH = MODEL_DIR / "pattern_model.joblib"
joblib.dump(modelo, MODEL_PATH)

print(f"\nModelo guardado en: {MODEL_PATH}")

# 9. Probar una lectura nueva
lectura = pd.DataFrame([{
    "temperature": 110.0,
    "vibration": 7.8,
    "pressure": 55.0,
    "fuel": 18.0,
}])

cluster = modelo.predict(lectura)[0]

print("\nPrueba con lectura nueva:")
print(lectura.to_string(index=False))
print(f"Cluster asignado: {cluster}")
