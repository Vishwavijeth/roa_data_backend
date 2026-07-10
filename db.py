import logging
import os
import threading
from psycopg2.pool import PoolError, ThreadedConnectionPool
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_pool = None
_pool_lock = threading.Lock()


def get_pool():
    global _pool

    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ThreadedConnectionPool(
                    minconn=int(os.getenv("DB_MIN_CONN", "2")),
                    maxconn=int(os.getenv("DB_MAX_CONN", "20")),
                    host=os.getenv("DB_HOST"),
                    port=os.getenv("DB_PORT"),
                    dbname=os.getenv("DB_NAME"),
                    user=os.getenv("DB_USER"),
                    password=os.getenv("DB_PASSWORD"),
                    connect_timeout=10,
                )

                logger.info("Database connection pool initialized.")

    return _pool


def close_pool():
    global _pool

    if _pool:
        logger.info("Closing database connection pool...")
        _pool.closeall()
        _pool = None


def _is_connection_alive(conn):
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return True
    except Exception:
        return False


class ConnectionWrapper:
    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool
        self._closed = False

    def __getattr__(self, item):
        return getattr(self._conn, item)

    def close(self):
        if self._closed:
            return

        try:
            if not self._conn.closed:
                try:
                    self._conn.rollback()
                except Exception:
                    pass

                self._pool.putconn(self._conn)

            else:
                self._pool.putconn(self._conn, close=True)

        except Exception:
            logger.exception("Failed returning DB connection to pool.")

            try:
                self._pool.putconn(self._conn, close=True)
            except Exception:
                pass

        finally:
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type:
                self._conn.rollback()
            else:
                self._conn.commit()
        finally:
            self.close()


def get_conn():
    pool = get_pool()

    try:
        conn = pool.getconn()

    except PoolError:
        logger.exception("Database connection pool exhausted.")
        raise

    if conn.closed or not _is_connection_alive(conn):
        logger.warning("Discarding dead database connection.")

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