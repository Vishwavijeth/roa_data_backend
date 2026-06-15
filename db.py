import os
import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from dotenv import load_dotenv

load_dotenv()  # loads .env locally

_pool = None

def get_pool():
    global _pool
    if _pool is None:
        minconn = int(os.getenv("DB_MIN_CONN", "1"))
        maxconn = int(os.getenv("DB_MAX_CONN", "20"))
        _pool = ThreadedConnectionPool(
            minconn,
            maxconn,
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            host=os.getenv("DB_HOST"),
            port=os.getenv("DB_PORT"),
        )
    return _pool

class ConnectionWrapper:
    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool
        self._closed = False

    def __getattr__(self, name):
        return getattr(self._conn, name)

    @property
    def closed(self):
        if self._closed:
            return 1
        try:
            return self._conn.closed
        except Exception:
            return 1

    def close(self):
        if not self._closed:
            is_dead = False
            try:
                if self._conn.closed != 0:
                    is_dead = True
                else:
                    self._conn.rollback()
            except Exception:
                is_dead = True
            
            self._pool.putconn(self._conn, close=is_dead)
            self._closed = True

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._conn.__exit__(exc_type, exc_val, exc_tb)

def get_conn():
    pool = get_pool()
    conn = pool.getconn()
    
    # Check if connection is closed or dead
    try:
        if conn.closed != 0:
            pool.putconn(conn, close=True)
            conn = pool.getconn()
    except Exception:
        try:
            pool.putconn(conn, close=True)
        except Exception:
            pass
        conn = pool.getconn()
        
    return ConnectionWrapper(conn, pool)

def get_db():
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
}