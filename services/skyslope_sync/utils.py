from typing import Any, Optional
from datetime import datetime
import json, logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger(__name__)

DEFAULT_SYNC_DATE = "2024-01-01"


def normalize_date(value: Any) -> Optional[str]:
    if value is None:
        return None

    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None

        clean = value.split("T")[0].split(" ")[0]
        date_formats = [
            "%Y-%m-%d",
            "%m/%d/%Y",
            "%d/%m/%Y",
            "%Y/%m/%d",
            "%m%d%Y",
        ]

        for fmt in date_formats:
            try:
                dt = datetime.strptime(clean, fmt)
                if dt.year < 1900:
                    return None
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None

    if isinstance(value, (int, float)):
        try:
            dt = datetime.fromtimestamp(value)
            return dt.strftime("%Y-%m-%d")
        except (ValueError, OSError):
            return None

    return None


def to_json_text(value: Any) -> Optional[str]:
    if value is None:
        return None

    if isinstance(value, (dict, list, tuple, set)):
        try:
            if isinstance(value, set):
                value = list(value)
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            return str(value)

    return value


def clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None

    value = to_json_text(value)
    if isinstance(value, str):
        value = value.strip()
        return value if value else None

    text = str(value).strip()
    return text if text else None


def clean_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None

    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None

    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def clean_decimal(value: Any) -> Optional[float]:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None

    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None

    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def clean_bool(value: Any) -> Optional[bool]:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}

    if isinstance(value, (int, float)):
        return bool(value)

    return None


def clean_guid(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().rstrip(":")
    return text or None


def clean_url(value: Any) -> Optional[str]:
    value = clean_text(value)
    if not value:
        return None

    if value.lower() in {"null", "none", "n/a", "na"}:
        return None

    return value

def get_last_sync_date(conn) -> str:
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT sync_date, sync_timestamp
                FROM skyslope_sync
                ORDER BY id DESC
                LIMIT 1
            """)
            row = cur.fetchone()

        if row and row[0]:
            sync_date = row[0]
            sync_timestamp = row[1]

            if sync_timestamp:
                combined = datetime(
                    sync_date.year,
                    sync_date.month,
                    sync_date.day,
                    sync_timestamp.hour,
                    0,
                    0,
                )
                sync_datetime_str = combined.strftime("%Y-%m-%dT%H:00:00")
            else:
                sync_datetime_str = sync_date.strftime("%Y-%m-%dT00:00:00")

            logger.info("Last sync datetime loaded from DB: %s", sync_datetime_str)
            return sync_datetime_str

    except Exception as e:
        logger.warning("Could not read sync datetime from DB, using default: %s", e)

    default_sync_datetime = f"{DEFAULT_SYNC_DATE}T00:00:00"
    logger.info("No sync datetime found in DB. Using default: %s", default_sync_datetime)
    return default_sync_datetime

def update_sync_date(conn, status: str = "success", error_message: Optional[str] = None) -> None:
    now = datetime.now()

    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO skyslope_sync (
                    sync_date,
                    sync_timestamp,
                    status,
                    error_message
                )
                VALUES (%s, NOW(), %s, %s)
                RETURNING id, sync_date, sync_timestamp, status
            """, (now.date(), status, error_message))
            inserted_row = cur.fetchone()

        conn.commit()

        logger.info(
            "Sync date committed to DB: id=%s sync_date=%s sync_timestamp=%s status=%s",
            inserted_row[0] if inserted_row else None,
            inserted_row[1] if inserted_row else None,
            inserted_row[2] if inserted_row else None,
            inserted_row[3] if inserted_row else None,
        )

    except Exception as e:
        conn.rollback()
        logger.error("Failed to insert sync date into DB. Rolled back transaction: %s", e, exc_info=True)
        raise