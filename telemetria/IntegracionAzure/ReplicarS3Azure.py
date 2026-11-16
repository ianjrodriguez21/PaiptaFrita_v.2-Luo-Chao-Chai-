
import json
import logging
import os
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import quote, unquote_plus, urlencode
from urllib.request import Request, urlopen

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
secrets = boto3.client("secretsmanager")

SECRET_NAME = os.environ["AZURE_SECRET_NAME"]
TELEMETRY_BUCKET = os.environ["S3_BUCKET_NAME"]
ANALYSIS_BUCKET = os.environ["ANALYSIS_BUCKET_NAME"]


def get_config():
    response = secrets.get_secret_value(
        SecretId=SECRET_NAME
    )
    return json.loads(response["SecretString"])


def get_access_token(config):
    url = (
        "https://login.microsoftonline.com/"
        f"{quote(config['AZURE_TENANT_ID'], safe='')}"
        "/oauth2/v2.0/token"
    )

    payload = urlencode({
        "client_id": config["AZURE_CLIENT_ID"],
        "client_secret": config["AZURE_CLIENT_SECRET"],
        "scope": "https://storage.azure.com/.default",
        "grant_type": "client_credentials"
    }).encode("utf-8")

    request = Request(
        url,
        data=payload,
        headers={
            "Content-Type":
                "application/x-www-form-urlencoded"
        },
        method="POST"
    )

    with urlopen(request, timeout=20) as response:
        result = json.load(response)

    return result["access_token"]


def resolve_destination(bucket, key):
    if (
        bucket == TELEMETRY_BUCKET
        and key.startswith("telemetria/")
    ):
        return "telemetria", key[len("telemetria/"):]

    if (
        bucket == ANALYSIS_BUCKET
        and key.startswith("analisis-historico/")
    ):
        return (
            "analisis-historico",
            key[len("analisis-historico/"):]
        )

    return None


def upload_to_azure(
    account, token, container, blob_name, data
):
    url = (
        f"https://{account}.blob.core.windows.net/"
        f"{container}/{quote(blob_name, safe='/')}"
    )

    now = format_datetime(
        datetime.now(timezone.utc),
        usegmt=True
    )

    request = Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "x-ms-version": "2023-11-03",
            "x-ms-date": now,
            "x-ms-blob-type": "BlockBlob",
            "Content-Type": "application/json",
            "Content-Length": str(len(data))
        },
        method="PUT"
    )

    with urlopen(request, timeout=60) as response:
        if response.status != 201:
            raise RuntimeError(
                "Azure no confirmo la creacion del blob"
            )


def lambda_handler(event, context):
    config = None
    token = None
    copied = 0
    skipped = 0

    for record in event.get("Records", []):
        if record.get("eventSource") != "aws:s3":
            skipped += 1
            continue

        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(
            record["s3"]["object"]["key"]
        )

        destination = resolve_destination(bucket, key)

        if not destination or not key.endswith(".json"):
            skipped += 1
            continue

        container, blob_name = destination

        if not blob_name:
            skipped += 1
            continue

        if config is None:
            config = get_config()
            token = get_access_token(config)

        params = {
            "Bucket": bucket,
            "Key": key
        }

        # Leer la version exacta cuando S3 la informa
        version_id = record["s3"]["object"].get(
            "versionId"
        )

        if version_id:
            params["VersionId"] = version_id

        response = s3.get_object(**params)
        data = response["Body"].read()

        upload_to_azure(
            config["AZURE_STORAGE_ACCOUNT"],
            token,
            container,
            blob_name,
            data
        )

        copied += 1
        logger.info(
            "Replicado correctamente: %s/%s",
            container,
            blob_name
        )

    return {
        "status": "completed",
        "copied": copied,
        "skipped": skipped
    }
