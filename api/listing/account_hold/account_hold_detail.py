from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from psycopg2.extras import RealDictCursor
from db import get_db
from common.response import Response
from api.listing.account_hold.base import (
    OpenInvoiceItem, AccountHoldDetailData, ARBalanceItem, TransactionItem
)

router = APIRouter()


def normalize_email(value):
    if not value:
        return None
    return str(value).strip().lower()


def has_account_hold_tag(agenttags):
    if not agenttags:
        return False
    return "accounthold" in {
        tag.strip().lower()
        for tag in str(agenttags).split(",")
        if tag.strip()
    }


def fetch_agent_detail_transactions(db, email: str):
    query = """
        WITH target_agent AS (
            SELECT
                LOWER(TRIM(u.primary_emailaddress)) AS normalized_email,
                LOWER(TRIM(u.display_name)) AS normalized_name
            FROM brokerage_engine_users u
            WHERE LOWER(TRIM(u.primary_emailaddress)) = LOWER(TRIM(%s))
            LIMIT 1
        ),
        brokerage_engine_transactions AS (
            SELECT
                be.transaction_identifier_transactionid AS transaction_id,
                be.property_address,
                be.transaction_status AS source_status,
                LOWER(TRIM(split_email)) AS normalized_email,
                'brokerage_engine' AS source_name
            FROM brokerage_engine be
            CROSS JOIN LATERAL regexp_split_to_table(COALESCE(be.buying_agent_email, ''), ',') AS split_email
            WHERE NULLIF(TRIM(split_email), '') IS NOT NULL
        ),
        otherincome_transaction_agents AS (
            SELECT
                oi.transaction_identifier_transactionid AS transaction_id,
                oi.property_address,
                oi.transaction_status AS source_status,
                LOWER(TRIM(split_agent)) AS normalized_name,
                'otherincome_transactions' AS source_name
            FROM otherincome_transactions oi
            CROSS JOIN LATERAL regexp_split_to_table(COALESCE(oi.agents, ''), ',') AS split_agent
            WHERE NULLIF(TRIM(split_agent), '') IS NOT NULL
        ),
        matched_be_transactions AS (
            SELECT
                bet.transaction_id,
                bet.property_address,
                bet.source_status,
                bet.source_name
            FROM brokerage_engine_transactions bet
            JOIN target_agent ta
              ON ta.normalized_email = bet.normalized_email
        ),
        matched_oi_transactions AS (
            SELECT
                oita.transaction_id,
                oita.property_address,
                oita.source_status,
                oita.source_name
            FROM otherincome_transaction_agents oita
            JOIN target_agent ta
              ON ta.normalized_name = oita.normalized_name
        ),
        matched_transactions AS (
            SELECT DISTINCT ON (transaction_id, source_name)
                transaction_id,
                property_address,
                source_status,
                source_name
            FROM (
                SELECT * FROM matched_be_transactions
                UNION ALL
                SELECT * FROM matched_oi_transactions
            ) combined
            ORDER BY transaction_id, source_name, property_address
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
            mt.transaction_id AS transaction_identifier_transactionid,
            mt.property_address,
            mt.source_name,
            mt.source_status,
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
          ON lrd.transactionid = mt.transaction_id
        ORDER BY mt.property_address, mt.transaction_id
    """

    try:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (email,))
            return cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Detail transaction query failed: {str(e)}")


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
            "be": row.get(config["be_key"]),
            "skyslope": row.get(config["skyslope_key"]),
        }

    return mismatch_details


@router.get("/account-hold/detail/{customer_id}", response_model=Response[AccountHoldDetailData])
async def get_account_hold_detail(customer_id: int, db=Depends(get_db)):
    try:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    u.display_name,
                    u.primary_emailaddress,
                    u.qb_customerid,
                    u.agenttags
                FROM brokerage_engine_users u
                WHERE u.qb_customerid = %s
                LIMIT 1
                """,
                (customer_id,),
            )
            agent = cur.fetchone()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent lookup failed: {str(e)}")

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    rows = fetch_agent_detail_transactions(db, agent["primary_emailaddress"])

    ar_balance_row = None
    has_ar_balance = False

    try:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT abd.raw_invoice, abd.updated_at
                FROM ar_balance_details abd
                WHERE abd.customer_id = %s
                ORDER BY abd.updated_at DESC NULLS LAST
                LIMIT 1
                """,
                (customer_id,),
            )
            row = cur.fetchone()

        if row and row.get("raw_invoice"):
            raw_invoice = row["raw_invoice"]

            open_invoices = [
                OpenInvoiceItem(
                    balance=inv.get("balance"),
                    due_date=inv.get("due_date"),
                    txn_date=inv.get("txn_date"),
                    total_amt=inv.get("total_amt"),
                    doc_number=inv.get("doc_number"),
                    invoice_id=inv.get("invoice_id"),
                )
                for inv in (raw_invoice.get("open_invoices") or [])
            ]

            balance_value = raw_invoice.get("balance")
            try:
                has_ar_balance = float(balance_value or 0) > 0
            except (TypeError, ValueError):
                has_ar_balance = False

            ar_balance_row = ARBalanceItem(
                balance=balance_value,
                open_invoices=open_invoices,
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AR balance lookup failed: {str(e)}")

    has_account_hold = has_account_hold_tag(agent.get("agenttags"))

    broker_flags = []
    if has_account_hold:
        broker_flags.append("account_hold")
    if has_ar_balance:
        broker_flags.append("ar_balance")

    transactions = []
    seen_transactions = set()

    for row in rows:
        transaction_id = row["transaction_identifier_transactionid"]
        dedupe_key = (transaction_id, row.get("source_name"))

        if dedupe_key in seen_transactions:
            continue

        per_transaction_flags = build_transaction_flags(row)
        mismatch_details = build_mismatch_details(row, per_transaction_flags)

        transactions.append(
            TransactionItem(
                transactionid=transaction_id,
                property_address=row["property_address"],
                source_table=row["be_source_table"] or row["source_name"],
                status=row["source_status"],
                saleguid=row["saleguid"],
                be_transaction_specialist=row["be_transaction_specialist"],
                skyslope_reviewer=row["skyslope_reviewer"],
                transaction_flags=per_transaction_flags,
                mismatch_details=mismatch_details,
            )
        )
        seen_transactions.add(dedupe_key)

    return Response(
        data=AccountHoldDetailData(
            display_name=agent["display_name"],
            primary_emailaddress=agent["primary_emailaddress"],
            customer_id=str(agent["qb_customerid"]) if agent.get("qb_customerid") is not None else None,
            transaction_count=len(transactions),
            broker_flags=broker_flags,
            ar_balance=ar_balance_row,
            transactions=transactions,
        )
    )