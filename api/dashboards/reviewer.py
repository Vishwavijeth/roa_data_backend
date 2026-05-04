from fastapi import APIRouter, Query
from db import get_conn
from psycopg2.extras import RealDictCursor

router = APIRouter()

@router.get("/reviewer_dashboard")
def reviewer_dashboard(
    from_date: str = Query(None),
    to_date: str = Query(None),
    state: str = Query(None)
):
    conn = get_conn()

    try:
        query = """
        SELECT
            COALESCE(r.firstname || ' ' || r.lastname, 'Unassigned') AS reviewer_full_name,

            COUNT(*) FILTER (
                WHERE LOWER(COALESCE(s.status, '')) = 'pending'
            ) AS transactions_outstanding,

            COUNT(*) FILTER (
                WHERE LOWER(COALESCE(s.status, '')) = 'closed'
            ) AS transactions_closed

        FROM sale s
        LEFT JOIN users r
            ON s.reviewerguid = r.userguid
        LEFT JOIN sale_property sp
            ON s.saleguid = sp.saleguid

        WHERE 1=1
        """

        params = []

        if from_date:
            query += " AND s.escrowclosingdate >= %s"
            params.append(from_date)

        if to_date:
            query += " AND s.escrowclosingdate <= %s"
            params.append(to_date)

        if state:
            query += " AND LOWER(sp.state) = LOWER(%s)"
            params.append(state)

        query += """
        GROUP BY reviewer_full_name
        ORDER BY reviewer_full_name;
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

@router.get("/reviewer_dashboard/state")
def get_states():
    conn = get_conn()

    try:
        query = """
        SELECT DISTINCT state
        FROM sale_property
        WHERE state IS NOT NULL AND TRIM(state) <> ''
        ORDER BY state;
        """

        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()

        return {
            "data": [row[0] for row in rows]
        }

    finally:
        conn.close()