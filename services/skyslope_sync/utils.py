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

from sqlalchemy.orm import Session
from sqlalchemy import text

def get_last_sync_date(db: Session) -> str:
    try:
        row = db.execute(text("""
            SELECT sync_date, sync_timestamp
            FROM skyslope_sync
            ORDER BY id DESC
            LIMIT 1
        """)).mappings().first()

        if row and row["sync_date"]:
            sync_date = row["sync_date"]
            sync_datetime_str = sync_date.strftime("%Y-%m-%dT00:00:00")
            logger.info("Last sync datetime loaded from DB: %s", sync_datetime_str)
            return sync_datetime_str

    except Exception as e:
        logger.warning("Could not read sync datetime from DB, using default: %s", e)

    default_sync_datetime = f"{DEFAULT_SYNC_DATE}T00:00:00"
    logger.info("No sync datetime found in DB. Using default: %s", default_sync_datetime)
    return default_sync_datetime

def update_sync_date(db: Session, status: str = "success", error_message: Optional[str] = None) -> None:
    now = datetime.now()

    try:
        inserted_row = db.execute(text("""
            INSERT INTO skyslope_sync (
                sync_date,
                sync_timestamp,
                status,
                error_message
            )
            VALUES (:sync_date, NOW(), :status, :error_message)
            RETURNING id, sync_date, sync_timestamp, status
        """), {
            "sync_date": now.date(),
            "status": status,
            "error_message": error_message
        }).mappings().first()

        db.commit()

        logger.info(
            "Sync date committed to DB: id=%s sync_date=%s sync_timestamp=%s status=%s",
            inserted_row["id"] if inserted_row else None,
            inserted_row["sync_date"] if inserted_row else None,
            inserted_row["sync_timestamp"] if inserted_row else None,
            inserted_row["status"] if inserted_row else None,
        )

    except Exception as e:
        db.rollback()
        logger.error("Failed to insert sync date into DB. Rolled back transaction: %s", e, exc_info=True)
        raise