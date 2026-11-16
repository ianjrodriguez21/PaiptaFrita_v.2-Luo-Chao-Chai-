
"""
Lambda: ReplicarS3Azure

Copia JSON desde S3 a Azure Blob Storage.

Origen:
  Bucket S3 de telemetria, prefijo telemetria/
  Bucket S3 historico, prefijo analisis-historico/

Destino:
  Azure container telemetria
  Azure container analisis-historico

Credenciales:
  AWS Secrets Manager
"""

import json
import logging
import os
from urllib.parse import unquote_plus

import boto3
from azure.identity import ClientSecretCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
secrets = boto3.client("secretsmanager")

SECRET_NAME = os.environ["AZURE_SECRET_NAME"]
TELEMETRY_BUCKET = os.environ["S3_BUCKET_NAME"]
ANALYSIS_BUCKET = os.environ["ANALYSIS_BUCKET_NAME"]


def get_azure_client():
    response = secrets.get_secret_value(
        SecretId=SECRET_NAME
    )

    config = json.loads(response["SecretString"])

    credential = ClientSecretCredential(
        tenant_id=config["AZURE_TENANT_ID"],
        client_id=config["AZURE_CLIENT_ID"],
        client_secret=config["AZURE_CLIENT_SECRET"]
    )

    account_name = config["AZURE_STORAGE_ACCOUNT"]

    return BlobServiceClient(
        account_url=(
            f"https://{account_name}.blob.core.windows.net"
        ),
        credential=credential
    )


def resolve_destination(bucket, key):
    if (
        bucket == TELEMETRY_BUCKET
        and key.startswith("telemetria/")
    ):
        return (
            "telemetria",
            key.removeprefix("telemetria/")
        )

    if (
        bucket == ANALYSIS_BUCKET
        and key.startswith("analisis-historico/")
    ):
        return (
            "analisis-historico",
            key.removeprefix("analisis-historico/")
        )

    return None


def lambda_handler(event, context):
    azure = None
    copied = 0
    skipped = 0

    try:
        for record in event.get("Records", []):
            if record.get("eventSource") != "aws:s3":
                skipped += 1
                continue

            bucket = record["s3"]["bucket"]["name"]
            key = unquote_plus(
                record["s3"]["object"]["key"]
            )

            destination = resolve_destination(
                bucket, key
            )

            if not destination or not key.endswith(".json"):
                logger.info(
                    "Objeto ignorado: %s/%s",
                    bucket, key
                )
                skipped += 1
                continue

            container_name, blob_name = destination

            if not blob_name:
                skipped += 1
                continue

            if azure is None:
                azure = get_azure_client()

            # Leer exactamente el contenido del objeto S3
            s3_object = s3.get_object(
                Bucket=bucket,
                Key=key
            )

            data = s3_object["Body"].read()

            # Copiar a Azure sin alterar el JSON
            blob = azure.get_blob_client(
                container=container_name,
                blob=blob_name
            )

            blob.upload_blob(
                data,
                overwrite=True,
                content_settings=ContentSettings(
                    content_type="application/json"
                )
            )

            copied += 1

            logger.info(
                "Copiado a Azure: %s/%s",
                container_name,
                blob_name
            )

        return {
            "status": "completed",
            "copied": copied,
            "skipped": skipped
        }

    except Exception:
        logger.exception(
            "Error replicando objetos S3 hacia Azure"
        )
        raise
