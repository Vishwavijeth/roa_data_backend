from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from db import get_db
from datetime import timezone
from zoneinfo import ZoneInfo

router = APIRouter()

@router.get("/skyslope_sync_logs")
def get_skyslope_sync_logs(db: Session = Depends(get_db)):
    try:
        rows = db.execute(text("""
            SELECT 
                sync_date,
                sync_timestamp,
                status
            FROM skyslope_sync
            ORDER BY sync_timestamp DESC
        """)).mappings().all()

        result = []
        ist_timezone = ZoneInfo("Asia/Kolkata")

        for row in rows:
            sync_date = row["sync_date"]
            sync_timestamp = row["sync_timestamp"]
            status = row["status"]

            sync_time = None

            if sync_timestamp:
                utc_time = sync_timestamp.replace(tzinfo=timezone.utc)
                ist_time = utc_time.astimezone(ist_timezone)
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