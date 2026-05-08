import os
from pathlib import Path
from dotenv import load_dotenv
import psycopg
    
BACKEND_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BACKEND_DIR / ".env")
load_dotenv()

def getConnection() -> psycopg.Connection:
    required_vars = [
        "AZURE_DATABASE_HOST",
        "AZURE_DATABASE_PORT",
        "AZURE_DATABASE_NAME",
        "AZURE_DATABASE_USER",
        "AZURE_DATABASE_PASSWORD",
    ]
    missing = [name for name in required_vars if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "Missing Azure database environment variables: " + ", ".join(missing)
        )

    connection = psycopg.connect(
        host=os.getenv("AZURE_DATABASE_HOST"),
        port=os.getenv("AZURE_DATABASE_PORT"),
        dbname=os.getenv("AZURE_DATABASE_NAME"),
        user=os.getenv("AZURE_DATABASE_USER"),
        password=os.getenv("AZURE_DATABASE_PASSWORD"),
        sslmode="require"
    )
    return connection
