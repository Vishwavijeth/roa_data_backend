from fastapi import APIRouter
from db import get_conn
from datetime import timezone
from zoneinfo import ZoneInfo

router = APIRouter()

@router.get("/skyslope_sync_logs")
def get_skyslope_sync_logs():
    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT 
                sync_date,
                sync_timestamp,
                status
            FROM skyslope_sync
            ORDER BY sync_timestamp DESC
        """)

        rows = cur.fetchall()

        result = []

        ist_timezone = ZoneInfo("Asia/Kolkata")

        for row in rows:
            sync_date, sync_timestamp, status = row

            sync_time = None

            if sync_timestamp:
                # UTC -> IST
                utc_time = sync_timestamp.replace(tzinfo=timezone.utc)
                ist_time = utc_time.astimezone(ist_timezone)

                # only time
                sync_time = ist_time.strftime("%H:%M:%S")

            result.append({
                "sync_date": str(sync_date),
                "sync_time": sync_time,
                "status": status
            })

        return {
            "count": len(result),
            "data": result
        }

    except Exception as e:
        return {
            "error": str(e)
        }

    finally:
        cur.close()
        conn.close()