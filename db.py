import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()  # loads .env locally

def get_conn():
    db_url = os.getenv("DB_URL")

    if not db_url:
        raise Exception("DB_URL is missing")

    return psycopg2.connect(db_url, sslmode="require")