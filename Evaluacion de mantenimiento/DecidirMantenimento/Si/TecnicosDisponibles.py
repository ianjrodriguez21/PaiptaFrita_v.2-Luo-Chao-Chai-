
"""
AWS Lambda: TecnicosDisponibles

Busca técnicos disponibles para atender un incidente.

Tabla provisional DynamoDB: t_tecnicos
Partition Key: tenant_id
Sort Key: technician_id

No modifica registros ni asigna técnicos.
"""

import os
import math
import logging
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")

TECHNICIANS_TABLE_NAME = os.environ.get(
    "TECHNICIANS_TABLE_NAME",
    "t_tecnicos"
)

table = dynamodb.Table(TECHNICIANS_TABLE_NAME)


def haversine(lat1, lon1, lat2, lon2):
    """Calcula distancia geográfica en kilómetros."""

    earth_radius = 6371.0

    lat1, lon1, lat2, lon2 = map(
        math.radians,
        [lat1, lon1, lat2, lon2]
    )

    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(delta_lon / 2) ** 2
    )

    a = max(0.0, min(1.0, a))

    return 2 * earth_radius * math.asin(math.sqrt(a))


def validate_coordinates(latitude, longitude):
    """Valida coordenadas y devuelve floats."""

    try:
        lat = float(latitude)
        lon = float(longitude)
    except (ValueError, TypeError):
        raise ValueError("Coordenadas inválidas")

    if not (
        math.isfinite(lat)
        and math.isfinite(lon)
        and -90 <= lat <= 90
        and -180 <= lon <= 180
    ):
        raise ValueError("Coordenadas fuera de rango")

    return lat, lon


def get_required_specialties(event):
    """
    Obtiene especialidades explícitas o las infiere
    del tipo de incidente.
    """

    specialties = event.get("required_specialties")

    if specialties is None:
        specialty = event.get("required_specialty")

        if specialty:
            specialties = [specialty]

    if specialties is None:
        incident_type = event.get("incident_type", "")

        mapping = {
            "possible_bearing_failure": ["engine"],
            "possible_hydraulic_pressure_failure": ["hydraulics"],
            "abnormal_fuel_consumption": ["engine"],
            "abnormal_vibration": ["engine"],
        }

        specialties = mapping.get(incident_type, [])

    if isinstance(specialties, str):
        specialties = [specialties]

    if not isinstance(specialties, list):
        raise ValueError(
            "required_specialties debe ser una lista"
        )

    return sorted({
        s.strip().lower()
        for s in specialties
        if isinstance(s, str) and s.strip()
    })


def get_technicians(tenant_id):
    """Consulta todos los técnicos de un tenant."""

    technicians = []
    last_key = None

    while True:
        params = {
            "KeyConditionExpression": (
                Key("tenant_id").eq(tenant_id)
            )
        }

        if last_key:
            params["ExclusiveStartKey"] = last_key

        response = table.query(**params)

        technicians.extend(
            response.get("Items", [])
        )

        last_key = response.get("LastEvaluatedKey")

        if not last_key:
            break

    return technicians


def lambda_handler(event, context):

    try:
        # Recuperar datos desde Step Functions
        data = event.get("Payload", event)

        tenant_id = data.get("tenant_id")
        incident_id = data.get("incident_id")

        machine_data = data.get("machine_data") or {}

        latitude = data.get(
            "latitude",
            machine_data.get("latitude")
        )

        longitude = data.get(
            "longitude",
            machine_data.get("longitude")
        )

        if not tenant_id or not incident_id:
            raise ValueError(
                "tenant_id e incident_id son obligatorios"
            )

        machine_lat, machine_lon = validate_coordinates(
            latitude,
            longitude
        )

        required_specialties = get_required_specialties(data)

        # Configuración opcional
        max_distance_km = float(
            data.get("max_distance_km", 100)
        )

        max_results = int(
            data.get("max_results", 10)
        )

        if (
            not math.isfinite(max_distance_km)
            or max_distance_km <= 0
            or not 1 <= max_results <= 100
        ):
            raise ValueError(
                "max_distance_km o max_results inválidos"
            )

        # Consultar DynamoDB
        technicians = get_technicians(tenant_id)

        available_technicians = []

        for technician in technicians:

            if technician.get("availability") != "available":
                continue

            tech_id = technician.get("technician_id")

            if not tech_id:
                continue

            try:
                tech_lat, tech_lon = validate_coordinates(
                    technician.get("latitude"),
                    technician.get("longitude")
                )
            except ValueError:
                logger.warning(
                    "Técnico %s sin coordenadas válidas",
                    tech_id
                )
                continue

            specialties = technician.get(
                "specialties", []
            )

            specialties_normalized = {
                s.lower().strip()
                for s in specialties
                if isinstance(s, str)
            }

            # Compatibilidad: basta con una especialidad
            specialty_match = (
                not required_specialties
                or bool(
                    specialties_normalized.intersection(
                        required_specialties
                    )
                )
            )

            # No asignar candidatos incompatibles
            if not specialty_match:
                continue

            distance = haversine(
                machine_lat,
                machine_lon,
                tech_lat,
                tech_lon
            )

            if distance > max_distance_km:
                continue

            available_technicians.append({
                "technician_id": tech_id,
                "name": technician.get("name", ""),
                "distance_km": round(distance, 2),
                "specialty_match": specialty_match,
                "specialties": sorted(specialties_normalized),
                "availability": "available"
            })

        # Ordenar por cercanía
        available_technicians.sort(
            key=lambda t: (
                t["distance_km"],
                t["technician_id"]
            )
        )

        available_technicians = available_technicians[
            :max_results
        ]

        result = {
            "tenant_id": tenant_id,
            "incident_id": incident_id,
            "required_specialties": required_specialties,
            "available_technicians": available_technicians,
            "total_available": len(available_technicians),
            "technician_found": bool(available_technicians)
        }

        logger.info(
            "Incidente %s: %d técnicos candidatos",
            incident_id,
            len(available_technicians)
        )

        return result

    except Exception:
        logger.exception(
            "Error buscando técnicos disponibles"
        )
        raise
