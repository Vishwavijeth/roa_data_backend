from fastapi import APIRouter, Depends
import csv
import io
import httpx
import os
import re
import traceback
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional
from psycopg2.extras import execute_batch
from db import get_db

router = APIRouter()

BATCH_SIZE = 1000
OTHER_INCOME_CSV_URL = os.getenv("OTHER_INCOME_CSV_URL")

OTHER_INCOME_INSERT_SQL = """
INSERT INTO otherincome_transactions (
    transaction_identifier_transactionid,
    transaction_identifier_transactionguid,
    listingguid,
    property_address,
    transaction_status,
    address_line1,
    address_line2,
    city,
    state,
    zip,
    property_type,
    property_subtype,
    office,
    income_type,
    income_received_date,
    income_received,
    agents,
    gross_commission,
    agent_net,
    brokerage_net,
    agents_identifier,
    client_type,
    client_name,
    client_phone,
    client_email,
    tags,
    effective_at,
    finalized_date,
    transaction_specialist,
    skyslopefileid
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (transaction_identifier_transactionid)
DO UPDATE SET
    transaction_identifier_transactionguid = EXCLUDED.transaction_identifier_transactionguid,
    listingguid = EXCLUDED.listingguid,
    property_address = EXCLUDED.property_address,
    transaction_status = EXCLUDED.transaction_status,
    address_line1 = EXCLUDED.address_line1,
    address_line2 = EXCLUDED.address_line2,
    city = EXCLUDED.city,
    state = EXCLUDED.state,
    zip = EXCLUDED.zip,
    property_type = EXCLUDED.property_type,
    property_subtype = EXCLUDED.property_subtype,
    office = EXCLUDED.office,
    income_type = EXCLUDED.income_type,
    income_received_date = EXCLUDED.income_received_date,
    income_received = EXCLUDED.income_received,
    agents = EXCLUDED.agents,
    gross_commission = EXCLUDED.gross_commission,
    agent_net = EXCLUDED.agent_net,
    brokerage_net = EXCLUDED.brokerage_net,
    agents_identifier = EXCLUDED.agents_identifier,
    client_type = EXCLUDED.client_type,
    client_name = EXCLUDED.client_name,
    client_phone = EXCLUDED.client_phone,
    client_email = EXCLUDED.client_email,
    tags = EXCLUDED.tags,
    effective_at = EXCLUDED.effective_at,
    finalized_date = EXCLUDED.finalized_date,
    transaction_specialist = EXCLUDED.transaction_specialist,
    skyslopefileid = COALESCE(EXCLUDED.skyslopefileid, otherincome_transactions.skyslopefileid)
"""


def clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    return re.sub(r"\s+", " ", value)


def clean_decimal(value: Optional[str]):
    value = clean_text(value)
    if value is None:
        return None
    value = value.replace("$", "").replace(",", "")
    try:
        return Decimal(value)
    except InvalidOperation:
        raise ValueError(f"Invalid decimal value: {value}")


def clean_date(value: Optional[str]):
    value = clean_text(value)
    if value is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"Invalid date format: {value}. Expected YYYY-MM-DD")


def clean_uuid(value: Optional[str]) -> Optional[str]:
    value = clean_text(value)
    return value if value else None


def build_other_income_row(row: dict):
    return (
        clean_uuid(row.get("Transaction_Identifier_TransactionId")),
        clean_uuid(row.get("Transaction_Identifier_TransactionGuid")),
        clean_uuid(row.get("ListingGuid")),
        clean_text(row.get("Property_Address")),
        clean_text(row.get("Transaction_Status")),
        clean_text(row.get("Address_Line1")),
        clean_text(row.get("Address_Line2")),
        clean_text(row.get("City")),
        clean_text(row.get("State")),
        clean_text(row.get("Zip")),
        clean_text(row.get("Property_Type")),
        clean_text(row.get("Property_Subtype")),
        clean_text(row.get("Office")),
        clean_text(row.get("Income_Type")),
        clean_date(row.get("Income Received Date")),
        clean_decimal(row.get("Income_Received")),
        clean_text(row.get("Agents")),
        clean_decimal(row.get("Gross_Commission")),
        clean_decimal(row.get("Agent_Net")),
        clean_decimal(row.get("Brokerage_Net")),
        clean_text(row.get("Agents_Identifier")),
        clean_text(row.get("Client_Type")),
        clean_text(row.get("Client_Name")),
        clean_text(row.get("Client_Phone")),
        clean_text(row.get("Client_Email")),
        clean_text(row.get("Tags")),
        clean_date(row.get("Effective_At")),
        clean_date(row.get("Finalized_Date")),
        clean_text(row.get("Transaction Specialist")),
        clean_text(row.get("SkySlopeFileID"))
    )


@router.post("/sync/other-income")
async def sync_other_income(conn=Depends(get_db)):
    cur = conn.cursor()

    status = "failed"
    error_message = None
    total_upserted = 0
    errors = []

    try:
        if not OTHER_INCOME_CSV_URL:
            raise ValueError("OTHER_INCOME_CSV_URL is not configured")

        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            response = await client.get(OTHER_INCOME_CSV_URL)
            response.raise_for_status()

        reader = csv.DictReader(io.StringIO(response.text))
        batch = []

        for row_num, row in enumerate(reader, start=1):
            try:
                batch.append(build_other_income_row(row))

                if len(batch) >= BATCH_SIZE:
                    execute_batch(cur, OTHER_INCOME_INSERT_SQL, batch, page_size=BATCH_SIZE)
                    conn.commit()

                    total_upserted += len(batch)
                    batch = []

            except Exception as e:
                conn.rollback()
                errors.append(f"Row {row_num}: {e}")
                batch = []

        if batch:
            try:
                execute_batch(cur, OTHER_INCOME_INSERT_SQL, batch, page_size=BATCH_SIZE)
                conn.commit()

                total_upserted += len(batch)

            except Exception as e:
                conn.rollback()
                errors.append(f"Final batch error: {e}")

        if errors:
            status = "failed"
            error_message = "\n".join(errors)
        else:
            status = "success"

    except Exception as e:
        conn.rollback()
        traceback.print_exc()

        status = "failed"
        error_message = str(e)

    finally:
        try:
            now = datetime.now()

            cur.execute("""
                INSERT INTO otherincome_sync (
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