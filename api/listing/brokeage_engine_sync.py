from fastapi import APIRouter, Depends
import csv, io, httpx, os
from datetime import timezone
from zoneinfo import ZoneInfo
from sqlalchemy import text
from sqlalchemy.orm import Session
from db import get_db
from datetime import datetime
from services.be_sync_helpers import build_row_values, INSERT_SQL

router = APIRouter()

BATCH_SIZE = 1000
BE_CSV_URL = os.getenv("BE_CSV_URL")


@router.post("/sync/brokerage-engine")
async def sync_brokerage_engine(db: Session = Depends(get_db)):
    raw_conn = db.connection().connection
    cur = raw_conn.cursor()

    status = "failed"
    error_message = None
    total_upserted = 0
    errors = []

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.get(BE_CSV_URL)
            response.raise_for_status()

        reader = csv.DictReader(io.StringIO(response.text))
        batch = []

        for row_num, row in enumerate(reader, start=1):
            try:
                batch.append(build_row_values(row))

                if len(batch) >= BATCH_SIZE:
                    cur.executemany(INSERT_SQL, batch)
                    raw_conn.commit()

                    total_upserted += len(batch)
                    batch = []

            except Exception as e:
                raw_conn.rollback()
                errors.append(f"Row {row_num}: {e}")
                batch = []

        if batch:
            try:
                cur.executemany(INSERT_SQL, batch)
                raw_conn.commit()

                total_upserted += len(batch)

            except Exception as e:
                raw_conn.rollback()
                errors.append(f"Final batch error: {e}")

        if errors:
            status = "failed"
            error_message = "\n".join(errors)
        else:
            status = "success"

    except Exception as e:
        raw_conn.rollback()

        status = "failed"
        error_message = str(e)

    finally:
        try:
            now = datetime.now()

            cur.execute("""
                INSERT INTO brokerage_sync (
                    sync_date,
                    sync_timestamp,
                    status,
                    error_message
                )
                VALUES (%s, %s, %s, %s)
            """, (
                now.date(),
                now,
                status,
                error_message
            ))

            raw_conn.commit()

        except Exception:
            raw_conn.rollback()

        cur.close()

    return {
        "status": status,
        "total_upserted": total_upserted,
        "error_message": error_message
    }

@router.get("/brokerage_sync_logs")
def get_brokerage_sync_logs(db: Session = Depends(get_db)):
    try:
        rows = db.execute(text("""
            SELECT 
                sync_date,
                sync_timestamp,
                status
            FROM brokerage_sync
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