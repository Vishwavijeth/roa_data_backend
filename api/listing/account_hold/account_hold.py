from math import ceil
from typing import Literal
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg2.extras import RealDictCursor
from common.pagination import PaginationResponse
from db import get_db
from api.listing.account_hold.base import AccountHoldItem, AccountHoldSummaryData
from common.response import Response

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


def build_where_clause(
    search: str | None = None,
    account_hold: bool | None = None,
    ar_balance: bool | None = None,
    match_mode: str = "and",
):
    search_filters = []
    combinable_filters = []
    params = []

    if search and search.strip():
        search_value = f"%{search.strip()}%"
        search_filters.append(
            "(b.display_name ILIKE %s OR b.primary_emailaddress ILIKE %s)"
        )
        params.extend([search_value, search_value])

    if account_hold is True:
        combinable_filters.append("b.has_account_hold = TRUE")
    elif account_hold is False:
        combinable_filters.append("b.has_account_hold = FALSE")

    if ar_balance is True:
        combinable_filters.append("COALESCE(b.total_open_balance, 0) > 0")
    elif ar_balance is False:
        combinable_filters.append("COALESCE(b.total_open_balance, 0) <= 0")

    clauses = []
    if search_filters:
        clauses.append(" AND ".join(search_filters))

    if combinable_filters:
        joiner = " AND " if match_mode == "and" else " OR "
        combined = joiner.join(combinable_filters)
        if len(combinable_filters) > 1:
            combined = f"({combined})"
        clauses.append(combined)

    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_clause, params


def get_base_cte():
    return """
        WITH ar_summary AS (
            SELECT
                abd.customer_id AS customer_id,
                MAX(COALESCE(abd.total_open_balance, 0)) AS total_open_balance,
                MAX(COALESCE(abd.invoice_count, 0)) AS invoice_count,
                MAX(abd.updated_at) AS ar_updated_at
            FROM ar_balance_details abd
            GROUP BY abd.customer_id
        ),
        user_base AS (
            SELECT
                u.display_name,
                u.primary_emailaddress,
                u.agenttags,
                u.qb_customerid,
                EXISTS (
                    SELECT 1
                    FROM regexp_split_to_table(COALESCE(u.agenttags, ''), ',') AS tag
                    WHERE LOWER(TRIM(tag)) = 'accounthold'
                ) AS has_account_hold
            FROM brokerage_engine_users u
        ),
        b AS (
            SELECT
                ub.display_name,
                ub.primary_emailaddress,
                ub.agenttags,
                ub.qb_customerid,
                ub.has_account_hold,
                ar.customer_id AS matched_customer_id,
                COALESCE(ar.total_open_balance, 0) AS total_open_balance,
                COALESCE(ar.invoice_count, 0) AS invoice_count,
                ar.ar_updated_at,
                (COALESCE(ar.total_open_balance, 0) > 0) AS has_ar_balance
            FROM user_base ub
            LEFT JOIN ar_summary ar
              ON ar.customer_id = ub.qb_customerid
        )
    """


def fetch_agent_listing_page_base(
    db,
    page: int,
    size: int,
    search: str | None = None,
    account_hold: bool | None = None,
    ar_balance: bool | None = None,
    match_mode: str = "and",
):
    offset = (page - 1) * size
    where_clause, params = build_where_clause(
        search=search,
        account_hold=account_hold,
        ar_balance=ar_balance,
        match_mode=match_mode,
    )

    base_cte = get_base_cte()

    count_query = f"""
        {base_cte}
        SELECT COUNT(*) AS total_count
        FROM b
        {where_clause}
    """

    data_query = f"""
        {base_cte}
        SELECT
            b.display_name,
            b.primary_emailaddress,
            b.agenttags,
            b.qb_customerid,
            b.matched_customer_id,
            b.total_open_balance,
            b.invoice_count,
            b.ar_updated_at,
            b.has_account_hold,
            b.has_ar_balance
        FROM b
        {where_clause}
        ORDER BY
            b.has_account_hold DESC,
            b.total_open_balance DESC,
            b.display_name,
            b.primary_emailaddress
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
        raise HTTPException(status_code=500, detail=f"Listing base query failed: {str(e)}")


def fetch_transaction_summary_for_agents(db, agent_emails: list[str]):
    if not agent_emails:
        return {}

    query = """
        WITH target_agents AS (
            SELECT DISTINCT
                LOWER(TRIM(u.primary_emailaddress)) AS normalized_email,
                LOWER(TRIM(u.display_name)) AS normalized_name
            FROM brokerage_engine_users u
            WHERE u.primary_emailaddress IS NOT NULL
              AND TRIM(u.primary_emailaddress) <> ''
              AND LOWER(TRIM(u.primary_emailaddress)) = ANY(%s)
        ),
        brokerage_engine_transactions AS (
            SELECT
                be.transaction_identifier_transactionid AS transaction_id,
                LOWER(TRIM(split_email)) AS normalized_email
            FROM brokerage_engine be
            CROSS JOIN LATERAL regexp_split_to_table(COALESCE(be.buying_agent_email, ''), ',') AS split_email
            WHERE NULLIF(TRIM(split_email), '') IS NOT NULL
        ),
        otherincome_transaction_agents AS (
            SELECT
                oi.transaction_identifier_transactionid AS transaction_id,
                LOWER(TRIM(split_agent)) AS normalized_name
            FROM otherincome_transactions oi
            CROSS JOIN LATERAL regexp_split_to_table(COALESCE(oi.agents, ''), ',') AS split_agent
            WHERE NULLIF(TRIM(split_agent), '') IS NOT NULL
        ),
        matched_be_transactions AS (
            SELECT DISTINCT ta.normalized_email, bet.transaction_id
            FROM target_agents ta
            JOIN brokerage_engine_transactions bet
              ON bet.normalized_email = ta.normalized_email
        ),
        matched_oi_transactions AS (
            SELECT DISTINCT ta.normalized_email, oita.transaction_id
            FROM target_agents ta
            JOIN otherincome_transaction_agents oita
              ON oita.normalized_name = ta.normalized_name
        ),
        matched_transactions AS (
            SELECT normalized_email, transaction_id FROM matched_be_transactions
            UNION
            SELECT normalized_email, transaction_id FROM matched_oi_transactions
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
        )
        SELECT
            mt.normalized_email,
            COUNT(DISTINCT mt.transaction_id) AS transaction_count,
            BOOL_OR(
                lrd.saleguid IS NULL
                OR (lrd.gross_commission_match IS NOT NULL AND LOWER(TRIM(lrd.gross_commission_match)) <> 'match')
                OR (lrd.close_date_match IS NOT NULL AND LOWER(TRIM(lrd.close_date_match)) <> 'match')
                OR (lrd.status_match IS NOT NULL AND LOWER(TRIM(lrd.status_match)) <> 'match')
                OR (lrd.sale_price_match IS NOT NULL AND LOWER(TRIM(lrd.sale_price_match)) <> 'match')
            ) AS has_transaction_mismatch
        FROM matched_transactions mt
        LEFT JOIN latest_reconciliation_data lrd
          ON lrd.transactionid = mt.transaction_id
        GROUP BY mt.normalized_email
    """

    try:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (agent_emails,))
            rows = cur.fetchall()

        return {
            row["normalized_email"]: {
                "transaction_count": int(row["transaction_count"] or 0),
                "has_transaction_mismatch": bool(row["has_transaction_mismatch"]),
            }
            for row in rows
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transaction summary query failed: {str(e)}")


def fetch_agent_by_email(db, email: str):
    query = """
        SELECT
            u.display_name,
            u.primary_emailaddress,
            u.agenttags,
            u.qb_customerid
        FROM brokerage_engine_users u
        WHERE LOWER(TRIM(u.primary_emailaddress)) = LOWER(TRIM(%s))
        LIMIT 1
    """

    try:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (email,))
            return cur.fetchone()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent lookup failed: {str(e)}")


def fetch_agent_ar_balance(db, qb_customerid):
    if qb_customerid is None:
        return None

    query = """
        SELECT
            abd.customer_id AS customer_id,
            MAX(COALESCE(abd.total_open_balance, 0)) AS total_open_balance,
            MAX(COALESCE(abd.invoice_count, 0)) AS invoice_count,
            MAX(abd.updated_at) AS updated_at
        FROM ar_balance_details abd
        WHERE abd.customer_id = %s
        GROUP BY abd.customer_id
    """

    try:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (qb_customerid,))
            row = cur.fetchone()

        if not row:
            return None

        return {
            "customer_id": str(row["customer_id"]),
            "total_open_balance": row["total_open_balance"],
            "invoice_count": int(row["invoice_count"] or 0),
            "updated_at": row["updated_at"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AR balance lookup failed: {str(e)}")
    

@router.get("/account-hold/summary", response_model=Response[AccountHoldSummaryData])
async def get_account_hold_summary(db=Depends(get_db)):
    base_cte = get_base_cte()

    query = f"""
        {base_cte}
        SELECT
            COUNT(*) AS total_agents,
            COUNT(*) FILTER (WHERE b.has_ar_balance) AS agents_with_ar_balance,
            COUNT(*) FILTER (WHERE b.has_account_hold) AS agents_with_account_hold
        FROM b
    """

    try:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            row = cur.fetchone()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Summary query failed: {str(e)}")

    return Response(
        data=AccountHoldSummaryData(
            total_agents=int(row["total_agents"] or 0),
            agents_with_ar_balance=int(row["agents_with_ar_balance"] or 0),
            agents_with_account_hold=int(row["agents_with_account_hold"] or 0),
        )
    )

@router.get("/account-hold", response_model=PaginationResponse[AccountHoldItem])
async def get_account_hold_listing(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    account_hold: bool | None = Query(None),
    ar_balance: bool | None = Query(None),
    match_mode: Literal["and", "or"] = Query("and"),
    search: str | None = Query(None, max_length=100),
    db=Depends(get_db),
):
    total_count, agent_rows = fetch_agent_listing_page_base(
        db=db,
        page=page,
        size=size,
        search=search,
        account_hold=account_hold,
        ar_balance=ar_balance,
        match_mode=match_mode,
    )

    agent_emails = [
        normalize_email(row["primary_emailaddress"])
        for row in agent_rows
        if row.get("primary_emailaddress")
    ]

    transaction_summary_map = fetch_transaction_summary_for_agents(db, agent_emails)

    data = []
    for row in agent_rows:
        normalized_email = normalize_email(row.get("primary_emailaddress"))
        tx_summary = transaction_summary_map.get(normalized_email, {})

        total_open_balance = float(row.get("total_open_balance") or 0)
        has_account_hold = bool(row.get("has_account_hold"))
        has_ar_balance = total_open_balance > 0

        broker_flags = []
        if has_account_hold:
            broker_flags.append("account_hold")
        if has_ar_balance:
            broker_flags.append("ar_balance")

        transaction_flags = []
        if bool(tx_summary.get("has_transaction_mismatch", False)):
            transaction_flags.append("transaction_mismatch")

        data.append(
            AccountHoldItem(
                display_name=row["display_name"],
                primary_emailaddress=row["primary_emailaddress"],
                customer_id=str(row["qb_customerid"]) if row.get("qb_customerid") is not None else None,
                transaction_count=int(tx_summary.get("transaction_count") or 0),
                broker_flags=broker_flags,
                transaction_flags=transaction_flags,
            )
        )

    total_pages = ceil(total_count / size) if total_count else 1

    return PaginationResponse(
        data=data,
        page=page,
        page_size=size,
        count=len(data),
        total_count=total_count,
        total_pages=total_pages,
        has_next=page < total_pages,
    )