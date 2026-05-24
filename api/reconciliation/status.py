from fastapi import APIRouter, Query
from db import get_conn
from psycopg2.extras import RealDictCursor

router = APIRouter()

STATUS_BASE_QUERY = """
WITH base AS (
    SELECT
        be.skyslopefileid AS skyslopefileid,
        s.saleguid,

        be.transaction_identifier_transactionid AS transactionid,
        be.property_address AS propertyaddress,

        be.transaction_status AS be_status,
        s.status AS skyslope_status,

        CASE
            WHEN s.saleguid IS NULL
                THEN 'no_skyslope_record'

            WHEN LOWER(be.transaction_status) = 'cancelled'
                 AND LOWER(COALESCE(s.status, '')) IN ('canceled/app', 'canceled/pend')
                THEN 'match'

            WHEN LOWER(be.transaction_status) = 'closed'
                 AND LOWER(COALESCE(s.status, '')) = 'archived'
                THEN NULL

            WHEN LOWER(be.transaction_status) = LOWER(COALESCE(s.status, ''))
                THEN 'match'

            ELSE 'mismatch'
        END AS match_result

    FROM brokerage_engine be
    LEFT JOIN sale s
        ON s.saleguid::text = be.skyslopefileid::text
)
"""

@router.get("/compare/status/summary")
def status_summary():
    conn = get_conn()

    try:
        query = f"""
            {STATUS_BASE_QUERY}

            SELECT
                COUNT(*) AS total_count,

                COUNT(*) FILTER (WHERE match_result = 'match') AS match_count,
                COUNT(*) FILTER (WHERE match_result = 'mismatch') AS mismatch_count,
                COUNT(*) FILTER (WHERE match_result = 'no_skyslope_record') AS no_skyslope_record_count

            FROM base;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            result = cur.fetchone()

        match_count = result["match_count"] or 0
        mismatch_count = result["mismatch_count"] or 0

        comparison_total = match_count + mismatch_count

        return {
            "total_count": result["total_count"],
            "match_percentage": round((match_count / comparison_total) * 100, 2) if comparison_total else 0,
            "mismatch_percentage": round((mismatch_count / comparison_total) * 100, 2) if comparison_total else 0,
            "no_skyslope_record_count": result["no_skyslope_record_count"]

        }

    finally:
        conn.close()

@router.get("/compare/status")
def status(
    page: int = Query(default=1, ge=1),
    mismatch: bool = Query(default=False),
    no_skyslope: bool = Query(default=False)
):
    conn = get_conn()

    try:
        limit = 50
        offset = (page - 1) * limit

        conditions = []

        if mismatch:
            conditions.append("match_result = 'mismatch'")

        if no_skyslope:
            conditions.append("match_result = 'no_skyslope_record'")

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            {STATUS_BASE_QUERY}

            SELECT
                saleguid,
                transactionid,
                propertyaddress,
                be_status,
                skyslope_status,
                match_result
            FROM base
            {where_clause}
            ORDER BY saleguid
            LIMIT %s OFFSET %s;
        """

        count_query = f"""
            {STATUS_BASE_QUERY}

            SELECT COUNT(*) AS total_count
            FROM base
            {where_clause};
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(count_query)
            total_count = cur.fetchone()["total_count"]

            cur.execute(query, (limit, offset))
            rows = cur.fetchall()

        return {
            "page": page,
            "page_size": limit,
            "total_count": total_count,
            "total_pages": (total_count + limit - 1) // limit,
            "data": rows
        }

    finally:
        conn.close()