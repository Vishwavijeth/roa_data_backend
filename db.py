import logging
import os
from contextlib import contextmanager
from typing import Generator

from dotenv import load_dotenv
from psycopg2.pool import PoolError, ThreadedConnectionPool
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, declarative_base, sessionmaker

load_dotenv()

logger = logging.getLogger(__name__)

Base = declarative_base()

DB_MIN_CONN = int(os.getenv("DB_MIN_CONN", "2"))
DB_MAX_CONN = int(os.getenv("DB_MAX_CONN", "20"))

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
}

DATABASE_URL = URL.create(
    drivername="postgresql+psycopg2",
    username=DB_CONFIG["user"],
    password=DB_CONFIG["password"],
    host=DB_CONFIG["host"],
    port=DB_CONFIG["port"],
    database=DB_CONFIG["dbname"],
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=DB_MIN_CONN,
    max_overflow=max(0, DB_MAX_CONN - DB_MIN_CONN),
    pool_recycle=1800,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

_raw_pool: ThreadedConnectionPool | None = None


def init_raw_pool() -> ThreadedConnectionPool:
    global _raw_pool

    if _raw_pool is None:
        _raw_pool = ThreadedConnectionPool(
            minconn=DB_MIN_CONN,
            maxconn=DB_MAX_CONN,
            host=DB_CONFIG["host"],
            port=DB_CONFIG["port"],
            dbname=DB_CONFIG["dbname"],
            user=DB_CONFIG["user"],
            password=DB_CONFIG["password"],
            connect_timeout=10,
        )
        logger.info("Raw psycopg2 connection pool initialized.")

    return _raw_pool


def close_raw_pool() -> None:
    global _raw_pool

    if _raw_pool is not None:
        logger.info("Closing raw psycopg2 connection pool...")
        _raw_pool.closeall()
        _raw_pool = None


def _is_connection_alive(conn) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return True
    except Exception:
        return False


class RawConnectionWrapper:
    def __init__(self, conn, pool: ThreadedConnectionPool):
        self._conn = conn
        self._pool = pool
        self._closed = False

    def __getattr__(self, item):
        return getattr(self._conn, item)

    def close(self) -> None:
        if self._closed:
            return

        try:
            if not self._conn.closed:
                self._pool.putconn(self._conn)
            else:
                self._pool.putconn(self._conn, close=True)
        except Exception:
            logger.exception("Failed returning raw DB connection to pool.")
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


def get_conn() -> RawConnectionWrapper:
    pool = init_raw_pool()

    try:
        conn = pool.getconn()
    except PoolError:
        logger.exception("Raw database connection pool exhausted.")
        raise

    if conn.closed or not _is_connection_alive(conn):
        logger.warning("Discarding dead raw database connection.")
        try:
            pool.putconn(conn, close=True)
        except Exception:
            pass
        conn = pool.getconn()

    return RawConnectionWrapper(conn, pool)


@contextmanager
def raw_conn_context():
    conn = get_conn()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables initialized.")


def dispose_db() -> None:
    logger.info("Disposing SQLAlchemy engine...")
    engine.dispose()
    close_raw_pool()


def run_sqlalchemy_query(db: Session, sql: str, params: dict | None = None):
    result = db.execute(text(sql), params or {})
    return result


@contextmanager
def session_cursor(db: Session):
    conn = db.connection()
    raw_conn = conn.connection
    cursor = raw_conn.cursor()
    try:
        yield cursor
    except Exception:
        db.rollback()
        raise
    finally:
        cursor.close()