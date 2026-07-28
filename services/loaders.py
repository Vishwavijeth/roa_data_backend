from datetime import timezone
from zoneinfo import ZoneInfo
from sqlalchemy import text
from sqlalchemy.orm import Session

IST = ZoneInfo("Asia/Kolkata")

def get_be_sync(db: Session = None):
    if db is None:
        return None

    row = db.execute(text("""
        SELECT sync_date, sync_timestamp
        FROM brokerage_sync
        ORDER BY sync_timestamp DESC
        LIMIT 1
    """)).mappings().first()

    if not row:
        return None

    sync_date = row["sync_date"]
    sync_ts = row["sync_timestamp"]
    sync_ts_str = None

    if sync_ts:
        sync_ts = sync_ts.replace(tzinfo=timezone.utc)
        sync_ts = sync_ts.astimezone(IST)
        sync_ts_str = sync_ts.strftime("%H:%M:%S")

    return {
        "sync_date": sync_date,
        "sync_timestamp": sync_ts_str
    }

def get_skyslope_sync(db: Session = None):
    if db is None:
        return None

    row = db.execute(text("""
        SELECT sync_date, sync_timestamp
        FROM skyslope_sync
        ORDER BY sync_timestamp DESC
        LIMIT 1
    """)).mappings().first()

    if not row:
        return None

    sync_date = row["sync_date"]
    sync_ts = row["sync_timestamp"]
    sync_ts_str = None

    if sync_ts:
        sync_ts = sync_ts.replace(tzinfo=timezone.utc)
        sync_ts = sync_ts.astimezone(IST)
        sync_ts_str = sync_ts.strftime("%H:%M:%S")

    return {
        "sync_date": sync_date,
        "sync_timestamp": sync_ts_str
    }