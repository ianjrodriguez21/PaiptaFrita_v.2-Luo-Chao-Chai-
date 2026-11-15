
from pathlib import Path
import numpy as np
import pandas as pd

# Semilla fija para obtener los mismos resultados
rng = np.random.default_rng(42)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

def generar_lecturas(cantidad, tipo):
    if tipo == "normal":
        temperatura = rng.normal(72, 6, cantidad)
        vibracion = rng.normal(2.4, 0.45, cantidad)
        presion = rng.normal(105, 9, cantidad)
        combustible = rng.normal(12, 1.7, cantidad)

    elif tipo == "sobrecalentamiento":
        temperatura = rng.normal(108, 7, cantidad)
        vibracion = rng.normal(3.3, 0.6, cantidad)
        presion = rng.normal(116, 10, cantidad)
        combustible = rng.normal(16, 2, cantidad)

    elif tipo == "vibracion_excesiva":
        temperatura = rng.normal(81, 7, cantidad)
        vibracion = rng.normal(7.5, 1, cantidad)
        presion = rng.normal(108, 10, cantidad)
        combustible = rng.normal(14, 2, cantidad)

    elif tipo == "presion_baja":
        temperatura = rng.normal(76, 6, cantidad)
        vibracion = rng.normal(3.1, 0.6, cantidad)
        presion = rng.normal(54, 9, cantidad)
        combustible = rng.normal(17, 2, cantidad)

    return pd.DataFrame({
        "temperature": np.clip(temperatura, 0, None),
        "vibration": np.clip(vibracion, 0, None),
        "pressure": np.clip(presion, 0, None),
        "fuel": np.clip(combustible, 0, None),
        "tipo": tipo,
        "anomaly": 0 if tipo == "normal" else 1,
    })


# 6000 lecturas normales y 600 anormales
datos = pd.concat([
    generar_lecturas(6000, "normal"),
    generar_lecturas(200, "sobrecalentamiento"),
    generar_lecturas(200, "vibracion_excesiva"),
    generar_lecturas(200, "presion_baja"),
], ignore_index=True)

# Mezclar las observaciones
datos = datos.sample(
    frac=1, random_state=42
).reset_index(drop=True)

# Guardar CSV
ruta = DATA_DIR / "telemetria_entrenamiento.csv"
datos.to_csv(ruta, index=False)

print("Dataset creado correctamente")
print(f"Archivo: {ruta}")
print(f"Total de lecturas: {len(datos)}")
print("\nTipos de lecturas:")
print(datos["tipo"].value_counts())
print("\nPrimeras 5 filas:")
print(datos.head().to_string(index=False))
