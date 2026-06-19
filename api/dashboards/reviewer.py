from fastapi import APIRouter, Query, Depends
from typing import Optional, List
from db import get_db
from psycopg2.extras import RealDictCursor

router = APIRouter()

from typing import Optional, List
from fastapi import Query, Depends
from psycopg2.extras import RealDictCursor


@router.get("/reviewer_dashboard")
def reviewer_dashboard(
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    state: Optional[List[str]] = Query(None),
    stage_name: Optional[List[str]] = Query(None),
    status: Optional[List[str]] = Query(None),
    reviewer: Optional[List[str]] = Query(None),
    type_of_sale: Optional[List[str]] = Query(None),
    conn=Depends(get_db)
):
    try:
        query = """
        SELECT
            COALESCE(NULLIF(TRIM(CONCAT_WS(' ', r.firstname, r.lastname)), ''), 'Unassigned') AS reviewer_full_name,

            COUNT(*) FILTER (
                WHERE LOWER(TRIM(COALESCE(s.status, ''))) = 'archived'
            ) AS transactions_archived,

            COUNT(*) FILTER (
                WHERE LOWER(TRIM(COALESCE(s.status, ''))) IN ('canceled/app', 'canceled/pend')
            ) AS transactions_canceled,

            COUNT(*) FILTER (
                WHERE LOWER(TRIM(COALESCE(s.status, ''))) = 'closed'
            ) AS transactions_closed,

            COUNT(*) FILTER (
                WHERE LOWER(TRIM(COALESCE(s.status, ''))) = 'expired'
            ) AS transactions_expired,

            COUNT(*) FILTER (
                WHERE LOWER(TRIM(COALESCE(s.status, ''))) = 'incomplete'
            ) AS transactions_incomplete,

            COUNT(*) FILTER (
                WHERE LOWER(TRIM(COALESCE(s.status, ''))) = 'pending'
            ) AS transactions_pending,

            COUNT(*) FILTER (
                WHERE LOWER(TRIM(COALESCE(s.status, ''))) = 'pre-contract'
            ) AS transactions_pre_contract,

            COUNT(*) FILTER (
                WHERE LOWER(TRIM(COALESCE(s.status, ''))) IN (
                    'archived',
                    'canceled/app',
                    'canceled/pend',
                    'closed',
                    'expired',
                    'incomplete',
                    'pending',
                    'pre-contract'
                )
            ) AS total_transactions

        FROM sale s
        LEFT JOIN users r
            ON s.reviewerguid = r.userguid
        LEFT JOIN sale_property sp
            ON s.saleguid = sp.saleguid
        LEFT JOIN stage st
            ON s.stageid = st.stageid
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
            cleaned_states = [x.strip() for x in state if x and x.strip()]
            if cleaned_states:
                query += " AND sp.state = ANY(%s)"
                params.append(cleaned_states)

        if stage_name:
            stage_name = [x for x in stage_name if x]
            if stage_name:
                query += " AND st.name = ANY(%s)"
                params.append(stage_name)

        if status:
            status = [x for x in status if x]
            if status:
                query += " AND s.status = ANY(%s)"
                params.append(status)

        if reviewer:
            reviewer = [x for x in reviewer if x]
            if reviewer:
                non_unassigned_reviewers = [x for x in reviewer if x != "Unassigned"]
                has_unassigned = "Unassigned" in reviewer

                reviewer_conditions = []

                if non_unassigned_reviewers:
                    reviewer_conditions.append("""
                        COALESCE(NULLIF(TRIM(CONCAT_WS(' ', r.firstname, r.lastname)), ''), 'Unassigned') = ANY(%s)
                    """)
                    params.append(non_unassigned_reviewers)

                if has_unassigned:
                    reviewer_conditions.append("s.reviewerguid IS NULL")

                if reviewer_conditions:
                    query += " AND (" + " OR ".join(reviewer_conditions) + ")"

        if type_of_sale:
            type_of_sale = [x for x in type_of_sale if x]
            if type_of_sale:
                query += " AND s.dealtype = ANY(%s)"
                params.append(type_of_sale)

        query += """
        GROUP BY reviewer_full_name
        ORDER BY reviewer_full_name
        """

        stage_query = """
            SELECT DISTINCT name
            FROM stage
            WHERE name IS NOT NULL
            ORDER BY name
        """

        state_query = """
            SELECT DISTINCT state
            FROM sale_property
            WHERE state IS NOT NULL AND state <> ''
            ORDER BY state
        """

        status_query = """
            SELECT DISTINCT status
            FROM sale
            WHERE status IS NOT NULL AND status <> ''
            ORDER BY status
        """

        reviewer_query = """
            SELECT DISTINCT reviewer_name
            FROM (
                SELECT
                    CASE
                        WHEN s.reviewerguid IS NULL THEN 'Unassigned'
                        ELSE COALESCE(NULLIF(TRIM(CONCAT_WS(' ', u.firstname, u.lastname)), ''), 'Unassigned')
                    END AS reviewer_name
                FROM sale s
                LEFT JOIN users u
                    ON s.reviewerguid = u.userguid
            ) x
            ORDER BY reviewer_name
        """

        type_of_sale_query = """
            SELECT DISTINCT dealtype
            FROM sale
            WHERE dealtype IS NOT NULL AND dealtype <> ''
            ORDER BY dealtype
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()

            cur.execute(stage_query)
            stage_list = [row["name"] for row in cur.fetchall()]

            cur.execute(state_query)
            state_list = [row["state"] for row in cur.fetchall()]

            cur.execute(status_query)
            status_list = [row["status"] for row in cur.fetchall()]

            cur.execute(reviewer_query)
            reviewer_list = [row["reviewer_name"] for row in cur.fetchall()]

            cur.execute(type_of_sale_query)
            type_of_sale_list = [row["dealtype"] for row in cur.fetchall()]

        return {
            "count": len(rows),
            "filters": {
                "stage_list": stage_list,
                "state_list": state_list,
                "status_list": status_list,
                "reviewer_list": reviewer_list,
                "type_of_sale_list": type_of_sale_list
            },
            "data": rows
        }

    finally:
        pass