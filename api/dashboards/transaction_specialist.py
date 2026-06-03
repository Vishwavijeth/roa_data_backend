from fastapi import APIRouter, Query
from db import get_conn
from psycopg2.extras import RealDictCursor

router = APIRouter()

from typing import Optional, List
from fastapi import Query
from psycopg2.extras import RealDictCursor


@router.get("/transaction_specialist_dashboard")
def transaction_specialist_dashboard(
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    state: Optional[List[str]] = Query(None)
):
    conn = get_conn()

    try:
        query = """
        SELECT
            COALESCE(be.transaction_specialist, 'Unassigned') AS transaction_specialist,

            COUNT(*) FILTER (
                WHERE LOWER(COALESCE(be.transaction_status, '')) = 'pending'
            ) AS transactions_outstanding,

            COUNT(*) FILTER (
                WHERE LOWER(COALESCE(be.transaction_status, '')) = 'closed'
            ) AS transactions_closed,

            COUNT(*) FILTER (
                WHERE LOWER(COALESCE(be.transaction_status, '')) = 'pending'
                AND LOWER(COALESCE(be.tags, '')) LIKE '%%open%%'
            ) AS open_count,

            COUNT(*) FILTER (
                WHERE LOWER(COALESCE(be.transaction_status, '')) = 'pending'
                AND LOWER(COALESCE(be.tags, '')) LIKE '%%commissionverified%%'
            ) AS commission_verified_count,

            COUNT(*) FILTER (
                WHERE LOWER(COALESCE(be.transaction_status, '')) = 'pending'
                AND LOWER(COALESCE(be.tags, '')) LIKE '%%cdasent%%'
            ) AS cda_sent_count,

            COUNT(*) FILTER (
                WHERE LOWER(COALESCE(be.transaction_status, '')) = 'pending'
                AND LOWER(COALESCE(be.tags, '')) LIKE '%%titlepaymentreceived%%'
            ) AS title_payment_received_count

        FROM brokerage_engine be
        WHERE 1=1
        """

        params = []

        if from_date:
            query += " AND be.closed_date::date >= %s"
            params.append(from_date)

        if to_date:
            query += " AND be.closed_date::date <= %s"
            params.append(to_date)

        if state:
            cleaned_states = [s.strip() for s in state if s and s.strip()]
            if cleaned_states:
                query += " AND LOWER(COALESCE(be.state, '')) = ANY(%s)"
                params.append([s.lower() for s in cleaned_states])

        query += """
        GROUP BY COALESCE(be.transaction_specialist, 'Unassigned')
        ORDER BY transaction_specialist;
        """

        states_query = """
        SELECT DISTINCT TRIM(state) AS state
        FROM brokerage_engine
        WHERE state IS NOT NULL
          AND TRIM(state) <> ''
        ORDER BY TRIM(state);
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()

        with conn.cursor() as cur:
            cur.execute(states_query)
            state_rows = cur.fetchall()

        states = [r[0] for r in state_rows]

        return {
            "count": len(rows),
            "states": states,
            "data": rows
        }

    finally:
        conn.close()