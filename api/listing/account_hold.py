from decimal import Decimal
from datetime import date, datetime
from uuid import UUID

from fastapi import Depends, APIRouter, HTTPException
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


@router.get("/account-hold")
async def get_account_hold_reconciliation(db=Depends(get_db)):
    query = """
        WITH target_agents AS (
            SELECT
                u.display_name,
                u.primary_emailaddress,
                LOWER(TRIM(u.display_name)) AS normalized_display_name
            FROM brokerage_engine_users u
            WHERE u.agenttags = 'AccountHold'
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
                sta.transaction_identifier_transactionid,
                sta.property_address,
                sta.source_name
            FROM target_agents ta
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
        ),

        enriched_transactions AS (
            SELECT
                mt.display_name,
                mt.primary_emailaddress,
                mt.transaction_identifier_transactionid,
                mt.property_address,
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
        )

        SELECT
            display_name,
            primary_emailaddress,
            transaction_identifier_transactionid,
            property_address,
            be_source_table,
            saleguid,
            be_transaction_specialist,
            skyslope_reviewer,
            be_gross_commission,
            skyslope_gross_commission,
            gross_commission_match,
            be_close_date_value,
            skyslope_close_date_value,
            close_date_match,
            be_status_value,
            skyslope_status_value,
            status_match,
            be_sale_price,
            skyslope_sale_price,
            sale_price_match
        FROM enriched_transactions
        ORDER BY display_name, property_address, transaction_identifier_transactionid
    """

    agents_query = """
        SELECT
            display_name,
            primary_emailaddress
        FROM brokerage_engine_users
        WHERE agenttags = 'AccountHold'
        ORDER BY display_name
    """

    try:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")

    try:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(agents_query)
            all_account_hold_agents = cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent query failed: {str(e)}")

    grouped_agents = {}

    for agent in all_account_hold_agents:
        agent_key = f"{agent['display_name']}|{agent['primary_emailaddress']}"
        grouped_agents[agent_key] = {
            "display_name": agent["display_name"],
            "primary_emailaddress": agent["primary_emailaddress"],
            "transactions": []
        }

    seen_transactions = set()

    for row in rows:
        display_name = row["display_name"]
        primary_emailaddress = row["primary_emailaddress"]
        transaction_id = serialize_value(row["transaction_identifier_transactionid"])

        agent_key = f"{display_name}|{primary_emailaddress}"
        seen_key = (agent_key, transaction_id)

        if agent_key not in grouped_agents:
            grouped_agents[agent_key] = {
                "display_name": display_name,
                "primary_emailaddress": primary_emailaddress,
                "transactions": []
            }

        if seen_key in seen_transactions:
            continue

        transaction_flags = build_transaction_flags(row)
        mismatch_details = build_mismatch_details(row, transaction_flags)

        grouped_agents[agent_key]["transactions"].append({
            "transactionid": transaction_id,
            "property_address": row["property_address"],
            "source_table": row["be_source_table"],
            "saleguid": serialize_value(row["saleguid"]),
            "be_transaction_specialist": row["be_transaction_specialist"],
            "skyslope_reviewer": row["skyslope_reviewer"],
            "transaction_flags": transaction_flags,
            "mismatch_details": mismatch_details
        })
        seen_transactions.add(seen_key)

    agents = list(grouped_agents.values())

    ar_input_rows = []
    for agent in agents:
        ar_input_rows.append({
            "display_name": agent["display_name"],
            "primary_emailaddress": agent["primary_emailaddress"],
            "email": agent["primary_emailaddress"],
        })

    ar_enriched_rows = await fetch_ar_balance(ar_input_rows, db)

    ar_map = {
        f"{row['display_name']}|{row['primary_emailaddress']}": row.get("ar_balance")
        for row in ar_enriched_rows
    }

    result = []
    for agent in agents:
        agent_key = f"{agent['display_name']}|{agent['primary_emailaddress']}"
        ar_balance = ar_map.get(agent_key)

        broker_flags = ["account_hold"]

        total_open_balance = 0
        if ar_balance and ar_balance.get("total_open_balance") is not None:
            total_open_balance = float(ar_balance["total_open_balance"])

        if total_open_balance > 0:
            broker_flags.append("ar_balance")

        result.append({
            "display_name": agent["display_name"],
            "primary_emailaddress": agent["primary_emailaddress"],
            "transaction_count": len(agent["transactions"]),
            "broker_flags": broker_flags,
            "ar_balance": ar_balance,
            "transactions": agent["transactions"]
        })

    return {
        "count": len(result),
        "data": result
    }