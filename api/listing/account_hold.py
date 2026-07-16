from decimal import Decimal
from datetime import date, datetime
from math import ceil
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg2.extras import RealDictCursor

from db import get_db
from services.account_hold_helper import fetch_ar_balance

router = APIRouter()


def serialize_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def has_account_hold_tag(agenttags):
    if not agenttags:
        return False

    tags = [tag.strip().lower() for tag in str(agenttags).split(",") if tag.strip()]
    return "accounthold" in tags


def build_transaction_flags(row):
    if row.get("saleguid") is None:
        return ["no_skyslope_file_id"]

    transaction_flags = []

    match_mapping = {
        "gross_commission_match": "gross_commission",
        "close_date_match": "close_date",
        "status_match": "status",
        "sale_price_match": "sale_price",
    }

    for db_field, response_flag in match_mapping.items():
        value = row.get(db_field)
        if value is not None and str(value).strip().lower() != "match":
            transaction_flags.append(response_flag)

    return transaction_flags


def build_mismatch_details(row, transaction_flags):
    if row.get("saleguid") is None:
        return {}

    mismatch_field_map = {
        "gross_commission": {
            "be_key": "be_gross_commission",
            "skyslope_key": "skyslope_gross_commission",
        },
        "close_date": {
            "be_key": "be_close_date_value",
            "skyslope_key": "skyslope_close_date_value",
        },
        "status": {
            "be_key": "be_status_value",
            "skyslope_key": "skyslope_status_value",
        },
        "sale_price": {
            "be_key": "be_sale_price",
            "skyslope_key": "skyslope_sale_price",
        },
    }

    mismatch_details = {}

    for flag in transaction_flags:
        config = mismatch_field_map.get(flag)
        if not config:
            continue

        mismatch_details[flag] = {
            "be": serialize_value(row.get(config["be_key"])),
            "skyslope": serialize_value(row.get(config["skyslope_key"])),
        }

    return mismatch_details


def fetch_agent_listing_page(db, page: int, size: int, has_account_hold=None, has_transaction_mismatch=None):
    offset = (page - 1) * size

    filters = []
    params = []

    if has_account_hold is not None:
        if has_account_hold:
            filters.append("COALESCE(a.agenttags, '') ILIKE '%AccountHold%'")
        else:
            filters.append("COALESCE(a.agenttags, '') NOT ILIKE '%AccountHold%'")

    if has_transaction_mismatch is not None:
        filters.append("COALESCE(ts.has_transaction_mismatch, FALSE) = %s")
        params.append(has_transaction_mismatch)

    where_clause = ""
    if filters:
        where_clause = "WHERE " + " AND ".join(filters)

    count_query = f"""
        WITH all_agents AS (
            SELECT
                u.display_name,
                u.primary_emailaddress,
                u.agenttags,
                LOWER(TRIM(u.display_name)) AS normalized_display_name
            FROM brokerage_engine_users u
        ),

        unified_transactions AS (
            SELECT
                be.transaction_identifier_transactionid,
                be.buying_agent_name AS raw_agent_names,
                'brokerage_engine' AS source_name
            FROM brokerage_engine be

            UNION ALL

            SELECT
                oi.transaction_identifier_transactionid,
                oi.agents AS raw_agent_names,
                'otherincome_transactions' AS source_name
            FROM otherincome_transactions oi
        ),

        split_transaction_agents AS (
            SELECT
                ut.transaction_identifier_transactionid,
                ut.source_name,
                LOWER(TRIM(split_name)) AS normalized_agent_name
            FROM unified_transactions ut
            CROSS JOIN LATERAL regexp_split_to_table(
                COALESCE(ut.raw_agent_names, ''),
                ','
            ) AS split_name
            WHERE NULLIF(TRIM(split_name), '') IS NOT NULL
        ),

        matched_transactions AS (
            SELECT DISTINCT ON (
                a.display_name,
                a.primary_emailaddress,
                sta.transaction_identifier_transactionid,
                sta.source_name
            )
                a.display_name,
                a.primary_emailaddress,
                sta.transaction_identifier_transactionid
            FROM all_agents a
            JOIN split_transaction_agents sta
              ON sta.normalized_agent_name = a.normalized_display_name
            ORDER BY
                a.display_name,
                a.primary_emailaddress,
                sta.transaction_identifier_transactionid,
                sta.source_name
        ),

        latest_reconciliation_data AS (
            SELECT DISTINCT ON (rd.transactionid)
                rd.transactionid,
                rd.saleguid,
                rd.gross_commission_match,
                rd.close_date_match,
                rd.status_match,
                rd.sale_price_match
            FROM reconciliation_data rd
            ORDER BY rd.transactionid, rd.evaluated_at DESC NULLS LAST
        ),

        transaction_summary AS (
            SELECT
                mt.display_name,
                mt.primary_emailaddress,
                COUNT(*) AS transaction_count,
                BOOL_OR(
                    lrd.saleguid IS NULL
                    OR (lrd.gross_commission_match IS NOT NULL AND LOWER(TRIM(lrd.gross_commission_match)) <> 'match')
                    OR (lrd.close_date_match IS NOT NULL AND LOWER(TRIM(lrd.close_date_match)) <> 'match')
                    OR (lrd.status_match IS NOT NULL AND LOWER(TRIM(lrd.status_match)) <> 'match')
                    OR (lrd.sale_price_match IS NOT NULL AND LOWER(TRIM(lrd.sale_price_match)) <> 'match')
                ) AS has_transaction_mismatch
            FROM matched_transactions mt
            LEFT JOIN latest_reconciliation_data lrd
              ON lrd.transactionid = mt.transaction_identifier_transactionid
            GROUP BY mt.display_name, mt.primary_emailaddress
        )

        SELECT COUNT(*) AS total_count
        FROM brokerage_engine_users a
        LEFT JOIN transaction_summary ts
          ON ts.display_name = a.display_name
         AND ts.primary_emailaddress = a.primary_emailaddress
        {where_clause}
    """

    data_query = f"""
        WITH all_agents AS (
            SELECT
                u.display_name,
                u.primary_emailaddress,
                u.agenttags,
                LOWER(TRIM(u.display_name)) AS normalized_display_name
            FROM brokerage_engine_users u
        ),

        unified_transactions AS (
            SELECT
                be.transaction_identifier_transactionid,
                be.buying_agent_name AS raw_agent_names,
                'brokerage_engine' AS source_name
            FROM brokerage_engine be

            UNION ALL

            SELECT
                oi.transaction_identifier_transactionid,
                oi.agents AS raw_agent_names,
                'otherincome_transactions' AS source_name
            FROM otherincome_transactions oi
        ),

        split_transaction_agents AS (
            SELECT
                ut.transaction_identifier_transactionid,
                ut.source_name,
                LOWER(TRIM(split_name)) AS normalized_agent_name
            FROM unified_transactions ut
            CROSS JOIN LATERAL regexp_split_to_table(
                COALESCE(ut.raw_agent_names, ''),
                ','
            ) AS split_name
            WHERE NULLIF(TRIM(split_name), '') IS NOT NULL
        ),

        matched_transactions AS (
            SELECT DISTINCT ON (
                a.display_name,
                a.primary_emailaddress,
                sta.transaction_identifier_transactionid,
                sta.source_name
            )
                a.display_name,
                a.primary_emailaddress,
                sta.transaction_identifier_transactionid
            FROM all_agents a
            JOIN split_transaction_agents sta
              ON sta.normalized_agent_name = a.normalized_display_name
            ORDER BY
                a.display_name,
                a.primary_emailaddress,
                sta.transaction_identifier_transactionid,
                sta.source_name
        ),

        latest_reconciliation_data AS (
            SELECT DISTINCT ON (rd.transactionid)
                rd.transactionid,
                rd.saleguid,
                rd.gross_commission_match,
                rd.close_date_match,
                rd.status_match,
                rd.sale_price_match
            FROM reconciliation_data rd
            ORDER BY rd.transactionid, rd.evaluated_at DESC NULLS LAST
        ),

        transaction_summary AS (
            SELECT
                mt.display_name,
                mt.primary_emailaddress,
                COUNT(*) AS transaction_count,
                BOOL_OR(
                    lrd.saleguid IS NULL
                    OR (lrd.gross_commission_match IS NOT NULL AND LOWER(TRIM(lrd.gross_commission_match)) <> 'match')
                    OR (lrd.close_date_match IS NOT NULL AND LOWER(TRIM(lrd.close_date_match)) <> 'match')
                    OR (lrd.status_match IS NOT NULL AND LOWER(TRIM(lrd.status_match)) <> 'match')
                    OR (lrd.sale_price_match IS NOT NULL AND LOWER(TRIM(lrd.sale_price_match)) <> 'match')
                ) AS has_transaction_mismatch
            FROM matched_transactions mt
            LEFT JOIN latest_reconciliation_data lrd
              ON lrd.transactionid = mt.transaction_identifier_transactionid
            GROUP BY mt.display_name, mt.primary_emailaddress
        )

        SELECT
            a.display_name,
            a.primary_emailaddress,
            a.agenttags,
            COALESCE(ts.transaction_count, 0) AS transaction_count,
            COALESCE(ts.has_transaction_mismatch, FALSE) AS has_transaction_mismatch
        FROM brokerage_engine_users a
        LEFT JOIN transaction_summary ts
          ON ts.display_name = a.display_name
         AND ts.primary_emailaddress = a.primary_emailaddress
        {where_clause}
        ORDER BY a.display_name, a.primary_emailaddress
        LIMIT %s OFFSET %s
    """

    try:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(count_query, params)
            total_count = int(cur.fetchone()["total_count"])

            cur.execute(data_query, params + [size, offset])
            rows = cur.fetchall()

        return total_count, rows
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Listing query failed: {str(e)}")


def fetch_agent_by_email(db, email: str):
    query = """
        SELECT
            u.display_name,
            u.primary_emailaddress,
            u.agenttags
        FROM brokerage_engine_users u
        WHERE LOWER(u.primary_emailaddress) = LOWER(%s)
        LIMIT 1
    """

    try:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (email,))
            return cur.fetchone()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent lookup failed: {str(e)}")


def fetch_agent_detail_transactions(db, email: str):
    query = """
        WITH target_agent AS (
            SELECT
                u.display_name,
                u.primary_emailaddress,
                u.agenttags,
                LOWER(TRIM(u.display_name)) AS normalized_display_name
            FROM brokerage_engine_users u
            WHERE LOWER(u.primary_emailaddress) = LOWER(%s)
        ),

        unified_transactions AS (
            SELECT
                be.transaction_identifier_transactionid,
                be.property_address,
                be.buying_agent_name AS raw_agent_names,
                'brokerage_engine' AS source_name
            FROM brokerage_engine be

            UNION ALL

            SELECT
                oi.transaction_identifier_transactionid,
                oi.property_address,
                oi.agents AS raw_agent_names,
                'otherincome_transactions' AS source_name
            FROM otherincome_transactions oi
        ),

        split_transaction_agents AS (
            SELECT
                ut.transaction_identifier_transactionid,
                ut.property_address,
                ut.source_name,
                LOWER(TRIM(split_name)) AS normalized_agent_name
            FROM unified_transactions ut
            CROSS JOIN LATERAL regexp_split_to_table(
                COALESCE(ut.raw_agent_names, ''),
                ','
            ) AS split_name
            WHERE NULLIF(TRIM(split_name), '') IS NOT NULL
        ),

        matched_transactions AS (
            SELECT DISTINCT ON (
                ta.display_name,
                ta.primary_emailaddress,
                sta.transaction_identifier_transactionid,
                sta.source_name
            )
                ta.display_name,
                ta.primary_emailaddress,
                ta.agenttags,
                sta.transaction_identifier_transactionid,
                sta.property_address,
                sta.source_name
            FROM target_agent ta
            JOIN split_transaction_agents sta
              ON sta.normalized_agent_name = ta.normalized_display_name
            ORDER BY
                ta.display_name,
                ta.primary_emailaddress,
                sta.transaction_identifier_transactionid,
                sta.source_name,
                sta.property_address
        ),

        latest_reconciliation_data AS (
            SELECT DISTINCT ON (rd.transactionid)
                rd.transactionid,
                rd.be_source_table,
                rd.saleguid,
                rd.be_transaction_specialist,
                rd.skyslope_reviewer,
                rd.be_gross_commission,
                rd.skyslope_gross_commission,
                rd.gross_commission_match,
                rd.be_close_date_value,
                rd.skyslope_close_date_value,
                rd.close_date_match,
                rd.be_status_value,
                rd.skyslope_status_value,
                rd.status_match,
                rd.be_sale_price,
                rd.skyslope_sale_price,
                rd.sale_price_match
            FROM reconciliation_data rd
            ORDER BY rd.transactionid, rd.evaluated_at DESC NULLS LAST
        )

        SELECT
            mt.display_name,
            mt.primary_emailaddress,
            mt.agenttags,
            mt.transaction_identifier_transactionid,
            mt.property_address,
            mt.source_name,
            lrd.be_source_table,
            lrd.saleguid,
            lrd.be_transaction_specialist,
            lrd.skyslope_reviewer,
            lrd.be_gross_commission,
            lrd.skyslope_gross_commission,
            lrd.gross_commission_match,
            lrd.be_close_date_value,
            lrd.skyslope_close_date_value,
            lrd.close_date_match,
            lrd.be_status_value,
            lrd.skyslope_status_value,
            lrd.status_match,
            lrd.be_sale_price,
            lrd.skyslope_sale_price,
            lrd.sale_price_match
        FROM matched_transactions mt
        LEFT JOIN latest_reconciliation_data lrd
          ON lrd.transactionid = mt.transaction_identifier_transactionid
        ORDER BY mt.property_address, mt.transaction_identifier_transactionid
    """

    try:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (email,))
            return cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Detail transaction query failed: {str(e)}")


@router.get("/account-hold")
async def get_account_hold_listing(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=50),
    has_account_hold: bool | None = Query(None),
    has_transaction_mismatch: bool | None = Query(None),
    db=Depends(get_db)
):
    total_count, rows = fetch_agent_listing_page(
        db=db,
        page=page,
        size=size,
        has_account_hold=has_account_hold,
        has_transaction_mismatch=has_transaction_mismatch,
    )

    total_pages = ceil(total_count / size) if total_count else 1

    data = []
    for row in rows:
        broker_flags = []
        transaction_flags = []

        if has_account_hold_tag(row.get("agenttags")):
            broker_flags.append("account_hold")

        if row.get("has_transaction_mismatch"):
            transaction_flags.append("transaction_mismatch")

        data.append({
            "display_name": row["display_name"],
            "primary_emailaddress": row["primary_emailaddress"],
            "transaction_count": int(row["transaction_count"] or 0),
            "broker_flags": broker_flags,
            "transaction_flags": transaction_flags,
        })

    return {
        "count": len(data),
        "total_count": total_count,
        "page": page,
        "size": size,
        "total_pages": total_pages,
        "data": data,
    }


@router.get("/account-hold/detail/{email}")
async def get_account_hold_detail(email: str, db=Depends(get_db)):
    agent = fetch_agent_by_email(db, email)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    rows = fetch_agent_detail_transactions(db, email)

    transactions = []
    seen_transactions = set()

    for row in rows:
        transaction_id = serialize_value(row["transaction_identifier_transactionid"])
        if transaction_id in seen_transactions:
            continue

        transaction_flags = build_transaction_flags(row)
        mismatch_details = build_mismatch_details(row, transaction_flags)

        transactions.append({
            "transactionid": transaction_id,
            "property_address": row["property_address"],
            "source_table": row["be_source_table"] or row["source_name"],
            "saleguid": serialize_value(row["saleguid"]),
            "be_transaction_specialist": row["be_transaction_specialist"],
            "skyslope_reviewer": row["skyslope_reviewer"],
            "transaction_flags": transaction_flags,
            "mismatch_details": mismatch_details,
        })
        seen_transactions.add(transaction_id)

    ar_input_rows = [{
        "display_name": agent["display_name"],
        "primary_emailaddress": agent["primary_emailaddress"],
        "email": agent["primary_emailaddress"],
    }]

    ar_enriched_rows = await fetch_ar_balance(ar_input_rows, db)
    ar_balance = ar_enriched_rows[0].get("ar_balance") if ar_enriched_rows else None

    broker_flags = []
    if has_account_hold_tag(agent.get("agenttags")):
        broker_flags.append("account_hold")
    if any(tx.get("transaction_flags") for tx in transactions):
        broker_flags.append("transaction_mismatch")
    if ar_balance and ar_balance.get("total_open_balance") is not None:
        if float(ar_balance["total_open_balance"]) > 0:
            broker_flags.append("ar_balance")

    return {
        "display_name": agent["display_name"],
        "primary_emailaddress": agent["primary_emailaddress"],
        "transaction_count": len(transactions),
        "broker_flags": broker_flags,
        "ar_balance": ar_balance,
        "transactions": transactions,
    }