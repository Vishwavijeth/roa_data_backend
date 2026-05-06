from fastapi import APIRouter, HTTPException
import csv, io, httpx, os
from db import get_conn
from services.sync_helpers import build_row_values, INSERT_SQL

router = APIRouter()

BATCH_SIZE = 1000
BE_CSV_URL = os.getenv("BE_CSV_URL")


@router.post("/sync/brokerage-engine")
async def sync_brokerage_engine():
    """
    POST /sync/brokerage-engine

    • Downloads CSV from BE_CSV_URL.
    • Transforms each field (UUID / date / numeric / text).
    • Upserts rows in batches — inserts new, updates existing by
      transaction_identifier_transactionid.
    • Returns a summary: total upserted and any per-row errors.
    """
    errors: list[str] = []
    total_upserted = 0

    # 1. Fetch CSV ─────────────────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.get(BE_CSV_URL)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch CSV from remote URL: {exc}",
        )

    csv_text = response.text  # decoded string

    # 2. Parse CSV ─────────────────────────────────────────────────────────
    reader = csv.DictReader(
        io.StringIO(csv_text),
        # utf-8-sig BOM is already stripped by httpx decoding
    )

    # 3. Connect to DB ─────────────────────────────────────────────────────
    conn = get_conn()

    cur = conn.cursor()

    try:
        batch: list[list] = []

        for row_num, row in enumerate(reader, start=1):
            try:
                values = build_row_values(row)
                batch.append(values)

                if len(batch) >= BATCH_SIZE:
                    cur.executemany(INSERT_SQL, batch)
                    conn.commit()
                    total_upserted += len(batch)
                    batch = []

            except Exception as row_err:
                conn.rollback()
                errors.append(f"Row {row_num}: {row_err}")
                # Continue processing remaining rows instead of aborting
                batch = []

        # Flush remaining rows
        if batch:
            try:
                cur.executemany(INSERT_SQL, batch)
                conn.commit()
                total_upserted += len(batch)
            except Exception as flush_err:
                conn.rollback()
                errors.append(f"Final batch flush error: {flush_err}")

    finally:
        cur.close()
        conn.close()

    status = "success" if not errors else "partial"
    message = (
        f"Upsert completed. Total rows upserted: {total_upserted}."
        + (f" {len(errors)} row(s) had errors." if errors else "")
    )