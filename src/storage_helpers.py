"""Blob Storage helpers used by app.py's routes, kept out of the HTTP layer so upload/export
logic stays testable on its own.

Deliberately transport-agnostic: nothing here imports flask.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol

from azure.storage.blob import BlobSasPermissions, BlobServiceClient, generate_blob_sas


class BlobContainer(Protocol):
    def upload_blob(self, blob_name: str, data: bytes, overwrite: bool = True) -> None: ...


class ContainerAdapter:
    """Adapts a BlobContainerClient to the small `upload_blob(name, data, overwrite)` shape
    UploadHandler depends on (dependency inversion, keeps UploadHandler SDK-agnostic for tests)."""

    def __init__(self, client):
        self._client = client

    def upload_blob(self, blob_name: str, data: bytes, overwrite: bool = True) -> None:
        self._client.upload_blob(name=blob_name, data=data, overwrite=overwrite)


def get_container(blob_service: BlobServiceClient, name: str) -> ContainerAdapter:
    client = blob_service.get_container_client(name)
    if not client.exists():
        client.create_container()
    return ContainerAdapter(client)


def parse_account_key(connection_string: str) -> str:
    parts = dict(p.split("=", 1) for p in connection_string.split(";") if "=" in p)
    return parts["AccountKey"]


def write_export_and_get_sas(
    blob_service: BlobServiceClient, job_id: str, excel_bytes: bytes, connection_string: str
) -> str:
    blob_name = f"{job_id}/characteristics.xlsx"
    get_container(blob_service, "exports").upload_blob(blob_name, excel_bytes, overwrite=True)

    blob_client = blob_service.get_blob_client(container="exports", blob=blob_name)
    sas_token = generate_blob_sas(
        account_name=blob_service.account_name,
        container_name="exports",
        blob_name=blob_name,
        account_key=parse_account_key(connection_string),
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    return f"{blob_client.url}?{sas_token}"


def generate_upload_sas(
    blob_service: BlobServiceClient,
    connection_string: str,
    container_name: str,
    blob_name: str,
    expiry_hours: int = 1,
) -> str:
    """A write-only SAS URL the *client* uploads directly to, bypassing app.py entirely for the
    file payload. Exists specifically because Vercel caps request bodies at 4.5MB -- see app.py's
    module docstring."""
    get_container(blob_service, container_name)  # ensure it exists before handing out a SAS to it
    blob_client = blob_service.get_blob_client(container=container_name, blob=blob_name)
    sas_token = generate_blob_sas(
        account_name=blob_service.account_name,
        container_name=container_name,
        blob_name=blob_name,
        account_key=parse_account_key(connection_string),
        permission=BlobSasPermissions(write=True, create=True),
        expiry=datetime.now(timezone.utc) + timedelta(hours=expiry_hours),
    )
    return f"{blob_client.url}?{sas_token}"


def read_blob_bytes(blob_service: BlobServiceClient, container_name: str, blob_name: str) -> bytes:
    blob_client = blob_service.get_blob_client(container=container_name, blob=blob_name)
    return blob_client.download_blob().readall()
