import os
from dotenv import load_dotenv
import psycopg
    
load_dotenv()

def getConnection() -> psycopg.Connection:
    connection = psycopg.connect(
        host=os.getenv("AZURE_DATABASE_HOST"),
        port=os.getenv("AZURE_DATABASE_PORT"),
        dbname=os.getenv("AZURE_DATABASE_NAME"),
        user=os.getenv("AZURE_DATABASE_USER"),
        password=os.getenv("AZURE_DATABASE_PASSWORD"),
        sslmode="require"
    )
    return connection