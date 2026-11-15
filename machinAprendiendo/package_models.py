
from pathlib import Path
import tarfile

BASE_DIR = Path(__file__).resolve().parent
MODELS_DIR = BASE_DIR / "models"
OUTPUT_DIR = BASE_DIR / "artifacts"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MODELS = [
    {
        "name": "anomalies",
        "model": "anomaly_model.joblib",
        "inference": "inference_anomalies.py",
    },
    {
        "name": "patterns",
        "model": "pattern_model.joblib",
        "inference": "inference_patterns.py",
    },
]


def package_model(config):
    model_file = MODELS_DIR / config["model"]
    inference_file = BASE_DIR / config["inference"]

    if not model_file.is_file():
        raise FileNotFoundError(model_file)

    if not inference_file.is_file():
        raise FileNotFoundError(inference_file)

    output_file = OUTPUT_DIR / f"{config['name']}-model.tar.gz"

    with tarfile.open(output_file, "w:gz") as archive:
        archive.add(
            model_file,
            arcname=config["model"],
        )

        archive.add(
            inference_file,
            arcname="code/inference.py",
        )

    print(f"\nPaquete creado: {output_file}")

    with tarfile.open(output_file, "r:gz") as archive:
        print("Contenido:")
        for member in archive.getmembers():
            print(f"  {member.name}")


if __name__ == "__main__":
    for config in MODELS:
        package_model(config)

    print("\nAMBOS MODELOS EMPAQUETADOS CORRECTAMENTE")
