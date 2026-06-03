from fastapi import APIRouter, Query
from typing import Optional, List
from db import get_conn
from psycopg2.extras import RealDictCursor

router = APIRouter()

@router.get("/reviewer_dashboard")
def reviewer_dashboard(
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    state: Optional[List[str]] = Query(None)
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
            cleaned_states = [s.strip() for s in state if s and s.strip()]
            if cleaned_states:
                query += " AND sp.state = ANY(%s)"
                params.append(cleaned_states)

        query += """
        GROUP BY reviewer_full_name
        ORDER BY reviewer_full_name;
        """

        states_query = """
        SELECT DISTINCT TRIM(state) AS state
        FROM sale_property
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

        return {
            "count": len(rows),
            "states": [row[0] for row in state_rows],
            "data": rows
        }

    finally:
        conn.close()