from fastapi import APIRouter, Query
from db import get_conn
from psycopg2.extras import RealDictCursor

router = APIRouter()

@router.get("/transaction_specialist_dashboard")
def transaction_specialist_dashboard(
    from_date: str = Query(None),
    to_date: str = Query(None),
    state: str = Query(None)
):
    conn = get_conn()

    try:
        query = """
        SELECT
            COALESCE(be.transaction_specialist, 'Unassigned') AS transaction_specialist,

            -- outstanding
            COUNT(*) FILTER (
                WHERE NOT (
                    LOWER(COALESCE(be.tags, '')) LIKE '%%complete%%' OR
                    LOWER(COALESCE(be.tags, '')) LIKE '%%revoked%%'
                )
            ) AS transactions_outstanding,

            -- closed
            COUNT(*) FILTER (
                WHERE
                    LOWER(COALESCE(be.tags, '')) LIKE '%%complete%%' OR
                    LOWER(COALESCE(be.tags, '')) LIKE '%%revoked%%'
            ) AS transactions_closed,

            -- open
            COUNT(*) FILTER (
                WHERE LOWER(COALESCE(be.tags, '')) LIKE '%%open%%'
            ) AS open_count,

            -- commission verified
            COUNT(*) FILTER (
                WHERE LOWER(COALESCE(be.tags, '')) LIKE '%%commissionverified%%'
            ) AS commission_verified_count,

            -- cda sent
            COUNT(*) FILTER (
                WHERE LOWER(COALESCE(be.tags, '')) LIKE '%%cdasent%%'
            ) AS cda_sent_count,

            -- title payment received
            COUNT(*) FILTER (
                WHERE LOWER(COALESCE(be.tags, '')) LIKE '%%titlepaymentreceived%%'
            ) AS title_payment_received_count

        FROM brokerage_engine be
        WHERE 1=1
        """

        params = []

        # date filter
        if from_date:
            query += " AND be.closed_date::date >= %s"
            params.append(from_date)

        if to_date:
            query += " AND be.closed_date::date <= %s"
            params.append(to_date)

        # state filter
        if state:
            query += " AND LOWER(COALESCE(be.state, '')) = LOWER(%s)"
            params.append(state)

        query += """
        GROUP BY COALESCE(be.transaction_specialist, 'Unassigned')
        ORDER BY transaction_specialist;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()

        return {
            "count": len(rows),
            "data": rows
        }

    finally:
        conn.close()

@router.get("/transaction_specialist/state")
def get_states():
    conn = get_conn()

    try:
        query = """
        SELECT DISTINCT state
        FROM brokerage_engine
        WHERE state IS NOT NULL AND TRIM(state) <> ''
        ORDER BY state;
        """

        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()

        states = [r[0] for r in rows]

        return {
            "count": len(states),
            "data": states
        }

    finally:
        conn.close()