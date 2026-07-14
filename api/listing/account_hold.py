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
    transaction_flags = []

    match_mapping = {
        "gross_commission_match": "gross_commission",
        "close_date_match": "close_date",
        "status_match": "status",
        "sale_price_match": "sale_price",
        "listing_price_match": "listing_price",
        "contract_date_match": "contract_date",
        "buyer_name_match": "buyer_name",
        "seller_name_match": "seller_name",
        "buying_agent_match": "buying_agent",
        "title_company_match": "title_company",
    }

    for db_field, response_flag in match_mapping.items():
        value = row.get(db_field)
        if value is not None and str(value).strip().lower() != "match":
            transaction_flags.append(response_flag)

    return transaction_flags


@router.get("/account-hold")
async def get_account_hold_reconciliation(db=Depends(get_db)):
    query = """
        WITH be_transactions AS (
            SELECT
                u.display_name,
                u.primary_emailaddress,
                be.transaction_identifier_transactionid,
                be.property_address
            FROM brokerage_engine_users u
            JOIN brokerage_engine be
                ON lower(trim(u.primary_emailaddress)) = ANY(
                    string_to_array(
                        regexp_replace(lower(coalesce(be.buying_agent_email, '')), '\s+', '', 'g'),
                        ','
                    )
                )
            WHERE u.agenttags = 'AccountHold'
        ),
        oi_transactions AS (
            SELECT
                u.display_name,
                u.primary_emailaddress,
                oi.transaction_identifier_transactionid,
                oi.property_address
            FROM brokerage_engine_users u
            JOIN otherincome_transactions oi
                ON lower(trim(u.display_name)) = ANY(
                    string_to_array(
                        regexp_replace(lower(coalesce(oi.agents, '')), '\s*,\s*', ',', 'g'),
                        ','
                    )
                )
            WHERE u.agenttags = 'AccountHold'
        ),
        matched_transactions AS (
            SELECT * FROM be_transactions
            UNION ALL
            SELECT * FROM oi_transactions
        ),
        enriched_transactions AS (
            SELECT
                mt.display_name,
                mt.primary_emailaddress,
                mt.transaction_identifier_transactionid,
                mt.property_address,
                rd.be_source_table,
                rd.gross_commission_match,
                rd.close_date_match,
                rd.status_match,
                rd.sale_price_match,
                rd.listing_price_match,
                rd.contract_date_match,
                rd.buyer_name_match,
                rd.seller_name_match,
                rd.buying_agent_match,
                rd.title_company_match
            FROM matched_transactions mt
            LEFT JOIN reconciliation_data rd
                ON rd.transactionid = mt.transaction_identifier_transactionid
        )
        SELECT
            display_name,
            primary_emailaddress,
            transaction_identifier_transactionid,
            property_address,
            be_source_table,
            gross_commission_match,
            close_date_match,
            status_match,
            sale_price_match,
            listing_price_match,
            contract_date_match,
            buyer_name_match,
            seller_name_match,
            buying_agent_match,
            title_company_match
        FROM enriched_transactions
        ORDER BY display_name, property_address
    """

    try:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")

    grouped_agents = {}

    for row in rows:
        transaction_flags = build_transaction_flags(row)

        if not transaction_flags:
            continue

        display_name = row["display_name"]
        primary_emailaddress = row["primary_emailaddress"]
        agent_key = f"{display_name}|{primary_emailaddress}"

        if agent_key not in grouped_agents:
            grouped_agents[agent_key] = {
                "display_name": display_name,
                "primary_emailaddress": primary_emailaddress,
                "transactions": []
            }

        grouped_agents[agent_key]["transactions"].append({
            "transactionid": serialize_value(row["transaction_identifier_transactionid"]),
            "property_address": row["property_address"],
            "source_table": row["be_source_table"],
            "transaction_flags": transaction_flags
        })

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