from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
from fastapi import UploadFile
from datetime import datetime, timedelta
import uuid
import os
from dotenv import load_dotenv
load_dotenv()

AZURE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
CONTAINER_NAME = os.getenv("AZURE_STORAGE_CONTAINER_NAME")

blob_service_client = None

def get_blob_service_client():
    global blob_service_client
    if blob_service_client:
        return blob_service_client
    if not AZURE_CONNECTION_STRING:
        raise RuntimeError("Missing AZURE_STORAGE_CONNECTION_STRING.")
    blob_service_client = BlobServiceClient.from_connection_string(
        AZURE_CONNECTION_STRING
    )
    return blob_service_client

def getBlobSasUrl(blob_name: str):
    client = get_blob_service_client()
    sas_token = generate_blob_sas(
        account_name=client.account_name,
        container_name=CONTAINER_NAME,
        blob_name=blob_name,
        account_key=client.credential.account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.utcnow() + timedelta(minutes=10)
    )

    url = f"https://{client.account_name}.blob.core.windows.net/{CONTAINER_NAME}/{blob_name}?{sas_token}"

    return url

# Upload a resume
async def uploadResume(file: UploadFile, profile_id: int):
    print(f"Uploading resume for profile {profile_id}")
    try:
        client = get_blob_service_client()
        safe_filename = os.path.basename(file.filename)
        blob_name = f"professionals/{profile_id}/{uuid.uuid4()}_{safe_filename}"

        blob_client = client.get_blob_client(
            container=CONTAINER_NAME,
            blob=blob_name
        )

        blob_client.upload_blob(file.file, overwrite=True)

        print(f"Resume uploaded successfully as {blob_name}")

        return {
            "blob_name": blob_name,
            "url": getBlobSasUrl(blob_name),
        }
    except Exception as e:
        print(f"Error uploading resume: {e}")
        raise e
    
# Retrieve resume
async def getResume(profile_id: int):
    container_client = get_blob_service_client().get_container_client(CONTAINER_NAME)

    prefix = f"professionals/{profile_id}/"

    blobs = container_client.list_blobs(name_starts_with=prefix)

    latest_blob = None

    for blob in blobs:
        if latest_blob is None or blob.last_modified > latest_blob.last_modified:
            latest_blob = blob

    if not latest_blob:
        return None

    blobUrl= getBlobSasUrl(latest_blob.name)

    return {
        "blob_name": latest_blob.name,
        "url": blobUrl,
        "last_modified": latest_blob.last_modified
    }
