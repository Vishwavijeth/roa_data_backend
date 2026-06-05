from fastapi import APIRouter, Depends
import csv, io, httpx, os
from datetime import timezone
from zoneinfo import ZoneInfo
from db import get_db
from datetime import datetime
from services.sync_helpers import build_row_values, INSERT_SQL

router = APIRouter()

BATCH_SIZE = 1000
BE_CSV_URL = os.getenv("BE_CSV_URL")


@router.post("/sync/brokerage-engine")
async def sync_brokerage_engine(conn=Depends(get_db)):
    cur = conn.cursor()

    status = "failed"
    error_message = None
    total_upserted = 0
    errors = []

    try:
        # fetch CSV
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.get(BE_CSV_URL)
            response.raise_for_status()

        reader = csv.DictReader(io.StringIO(response.text))
        batch = []

        # process CSV
        for row_num, row in enumerate(reader, start=1):
            try:
                batch.append(build_row_values(row))

                if len(batch) >= BATCH_SIZE:
                    cur.executemany(INSERT_SQL, batch)
                    conn.commit()

                    total_upserted += len(batch)
                    batch = []

            except Exception as e:
                conn.rollback()
                errors.append(f"Row {row_num}: {e}")
                batch = []

        # flush remaining batch
        if batch:
            try:
                cur.executemany(INSERT_SQL, batch)
                conn.commit()

                total_upserted += len(batch)

            except Exception as e:
                conn.rollback()
                errors.append(f"Final batch error: {e}")

        # final status
        if errors:
            status = "failed"
            error_message = "\n".join(errors)
        else:
            status = "success"

    except Exception as e:
        conn.rollback()

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

            conn.commit()

        except Exception:
            conn.rollback()

        cur.close()

    return {
        "status": status,
        "total_upserted": total_upserted,
        "error_message": error_message
    }

@router.get("/brokerage_sync_logs")
def get_brokerage_sync_logs(conn=Depends(get_db)):
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT 
                sync_date,
                sync_timestamp,
                status
            FROM brokerage_sync
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