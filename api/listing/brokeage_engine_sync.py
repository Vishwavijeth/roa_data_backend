from fastapi import APIRouter, HTTPException
import csv, io, httpx, os
from db import get_conn
from datetime import datetime
from services.sync_helpers import build_row_values, INSERT_SQL

router = APIRouter()

BATCH_SIZE = 1000
BE_CSV_URL = os.getenv("BE_CSV_URL")


@router.post("/sync/brokerage-engine")
async def sync_brokerage_engine():
    conn = get_conn()
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
        conn.close()

    return {
        "status": status,
        "total_upserted": total_upserted,
        "error_message": error_message
    }