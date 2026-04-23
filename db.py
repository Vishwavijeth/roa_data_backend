import psycopg2

DB_CONFIG = {
    "host": "localhost",
    "port": "5432",
    "database": "roa_data_1",
    "user": "postgres",
    "password": "2621"
}

def get_conn():
    return psycopg2.connect(**DB_CONFIG)