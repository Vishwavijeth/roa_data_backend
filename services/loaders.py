from db import get_conn
from datetime import timezone
from zoneinfo import ZoneInfo    

IST = ZoneInfo("Asia/Kolkata")

def get_be_sync(conn=None):
    should_close = False
    if conn is None:
        conn = get_conn()
        should_close = True
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT sync_date, sync_timestamp
            FROM brokerage_sync
            ORDER BY sync_timestamp DESC
            LIMIT 1
        """)

        row = cur.fetchone()

        if not row:
            return None

        sync_date, sync_ts = row

        sync_ts_str = None

        if sync_ts:
            sync_ts = sync_ts.replace(tzinfo=timezone.utc)
            sync_ts = sync_ts.astimezone(IST)
            sync_ts_str = sync_ts.strftime("%H:%M:%S")

        return {
            "sync_date": sync_date,
            "sync_timestamp": sync_ts_str
        }

    finally:
        cur.close()
        if should_close:
            conn.close()

def get_skyslope_sync(conn=None):
    should_close = False
    if conn is None:
        conn = get_conn()
        should_close = True
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT sync_date, sync_timestamp
            FROM skyslope_sync
            ORDER BY sync_timestamp DESC
            LIMIT 1
        """)

        row = cur.fetchone()

        if not row:
            return None

        sync_date, sync_ts = row

        sync_ts_str = None

        if sync_ts:
            sync_ts = sync_ts.replace(tzinfo=timezone.utc)
            sync_ts = sync_ts.astimezone(IST)
            sync_ts_str = sync_ts.strftime("%H:%M:%S")

        return {
            "sync_date": sync_date,
            "sync_timestamp": sync_ts_str
        }

    finally:
        cur.close()
        if should_close:
            conn.close()