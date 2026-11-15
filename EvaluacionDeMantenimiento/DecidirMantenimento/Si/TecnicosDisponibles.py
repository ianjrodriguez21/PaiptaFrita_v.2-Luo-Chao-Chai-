
"""
Lambda: TecnicosDisponibles

Tabla: t_tecnicos
PK: tenant_id
SK: technician_id

Salida: technicians_result
No modifica datos.
"""

import os
import math
import logging

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")

TABLE_NAME = os.environ.get(
    "TECHNICIANS_TABLE_NAME", "t_tecnicos"
)

table = dynamodb.Table(TABLE_NAME)

SPECIALTY_MAP = {
    "possible_bearing_failure": ["engine"],
    "possible_hydraulic_pressure_failure": ["hydraulics"],
    "abnormal_fuel_consumption": ["engine"],
    "abnormal_vibration": ["engine"],
    "abnormal_temperature": ["engine"]
}


def coordinates(lat, lon):
    lat = float(lat)
    lon = float(lon)

    if not (
        math.isfinite(lat)
        and math.isfinite(lon)
        and -90 <= lat <= 90
        and -180 <= lon <= 180
    ):
        raise ValueError("Coordenadas invalidas")

    return lat, lon


def haversine(lat1, lon1, lat2, lon2):
    radius = 6371.0

    lat1, lon1, lat2, lon2 = map(
        math.radians,
        [lat1, lon1, lat2, lon2]
    )

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(dlon / 2) ** 2
    )

    return 2 * radius * math.asin(
        math.sqrt(min(1.0, max(0.0, a)))
    )


def query_technicians(tenant_id):
    items = []
    last_key = None

    while True:
        params = {
            "KeyConditionExpression": (
                Key("tenant_id").eq(tenant_id)
            ),
            "ConsistentRead": True
        }

        if last_key:
            params["ExclusiveStartKey"] = last_key

        response = table.query(**params)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")

        if not last_key:
            break

    return items


def lambda_handler(event, context):
    tenant_id = event.get("tenant_id")

    incident = event.get("incident_result") or {}
    machine = event.get("machine_data") or {}
    evaluation = event.get("evaluation_result") or {}

    incident_id = incident.get("incident_id")

    if not tenant_id or not incident_id:
        raise ValueError(
            "Falta tenant_id o incident_result.incident_id"
        )

    if incident.get("tenant_id") != tenant_id:
        raise ValueError("Tenant del incidente no coincide")

    machine_id = event.get("machine_id")

    if incident.get("machine_id") != machine_id:
        raise ValueError("Maquina del incidente no coincide")

    machine_lat, machine_lon = coordinates(
        machine.get("latitude"),
        machine.get("longitude")
    )

    incident_type = (
        incident.get("incident_type")
        or evaluation.get("incident_type")
    )

    required = event.get("required_specialties")

    if required is None:
        required = SPECIALTY_MAP.get(incident_type)

    # No inventar especialidades para fallas desconocidas
    if not required:
        return {
            "tenant_id": tenant_id,
            "incident_id": incident_id,
            "available_technicians": [],
            "required_specialties": [],
            "technician_found": False,
            "status": "specialty_undefined"
        }

    if isinstance(required, str):
        required = [required]

    if not isinstance(required, list):
        raise ValueError(
            "required_specialties debe ser una lista"
        )

    required = sorted({
        item.lower().strip()
        for item in required
        if isinstance(item, str) and item.strip()
    })

    if not required:
        raise ValueError("Especialidades vacias")

    max_distance = float(
        event.get("max_distance_km", 100)
    )

    max_results = int(
        event.get("max_results", 10)
    )

    if (
        not math.isfinite(max_distance)
        or max_distance <= 0
        or not 1 <= max_results <= 100
    ):
        raise ValueError("Limites de busqueda invalidos")

    candidates = []

    for technician in query_technicians(tenant_id):
        if technician.get("availability") != "available":
            continue

        specialties = technician.get("specialties", [])

        if not isinstance(specialties, list):
            continue

        normalized = {
            str(s).lower().strip()
            for s in specialties
        }

        if not normalized.intersection(required):
            continue

        technician_id = technician.get("technician_id")

        if not technician_id:
            continue

        try:
            tech_lat, tech_lon = coordinates(
                technician.get("latitude"),
                technician.get("longitude")
            )
        except (TypeError, ValueError):
            continue

        distance = haversine(
            machine_lat,
            machine_lon,
            tech_lat,
            tech_lon
        )

        if distance > max_distance:
            continue

        candidates.append({
            "technician_id": technician_id,
            "name": technician.get("name", ""),
            "distance_km": round(distance, 2),
            "specialty_match": True,
            "specialties": sorted(normalized),
            "availability": "available"
        })

    candidates.sort(
        key=lambda x: (
            x["distance_km"],
            x["technician_id"]
        )
    )

    candidates = candidates[:max_results]

    return {
        "tenant_id": tenant_id,
        "incident_id": incident_id,
        "required_specialties": required,
        "available_technicians": candidates,
        "total_available": len(candidates),
        "technician_found": bool(candidates),
        "status": (
            "available" if candidates
            else "no_technician_available"
        )
    }
