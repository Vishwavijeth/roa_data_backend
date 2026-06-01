from fastapi import APIRouter, Query
from db import get_conn
from psycopg2.extras import RealDictCursor

router = APIRouter()


CLOSE_DATE_BASE_QUERY = """
WITH base AS (
    SELECT
        be.skyslopefileid AS skyslopefileid,
        s.saleguid,

        be.transaction_identifier_transactionid AS transactionid,
        be.property_address AS propertyaddress,

        be.transaction_status AS be_transaction_status,
        s.status AS skyslope_status,

        s.escrowclosingdate AS skyslope_close_date,
        be.closed_date AS be_close_date,

        CASE
            WHEN s.saleguid IS NULL
                THEN 'no_skyslope_record'

            WHEN LOWER(be.transaction_status) = 'cancelled'
                 AND LOWER(COALESCE(s.status, '')) IN ('canceled/app', 'canceled/pend')
                THEN NULL

            WHEN be.transaction_status ILIKE 'cancelled'
                AND (
                    s.status ILIKE 'canceled/pend'
                    OR s.status ILIKE 'canceled/app'
                )
                THEN NULL

            WHEN s.escrowclosingdate IS DISTINCT FROM be.closed_date
                THEN 'mismatch'

            ELSE 'match'
        END AS match_result

    FROM brokerage_engine be
    LEFT JOIN sale s
        ON s.saleguid = be.skyslopefileid
)
"""

@router.get("/compare/close_date/summary")
def close_date_summary():
    conn = get_conn()

    try:
        query = f"""
            {CLOSE_DATE_BASE_QUERY}

            SELECT
                COUNT(*) AS total_count,

                COUNT(*) FILTER (
                    WHERE match_result = 'match'
                ) AS match_count,

                COUNT(*) FILTER (
                    WHERE match_result = 'mismatch'
                ) AS mismatch_count,

                COUNT(*) FILTER (
                    WHERE match_result = 'no_skyslope_record'
                ) AS no_skyslope_record_count

            FROM base;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            result = cur.fetchone()

        match_count = result["match_count"] or 0
        mismatch_count = result["mismatch_count"] or 0

        comparison_total = match_count + mismatch_count

        match_percentage = (
            round((match_count / comparison_total) * 100, 2)
            if comparison_total else 0
        )

        mismatch_percentage = (
            round((mismatch_count / comparison_total) * 100, 2)
            if comparison_total else 0
        )

        return {
            "total_count": result["total_count"],
            "match_percentage": match_percentage,
            "mismatch_percentage": mismatch_percentage,
            "no_skyslope_record_count": result["no_skyslope_record_count"]
        }

    finally:
        conn.close()


@router.get("/compare/close_date")
def close_date(
    page: int = Query(default=1, ge=1),
    mismatch: bool = Query(default=False),
    track_status: str = Query(default=None),
    search: str = Query(default=None)
):
    conn = get_conn()

    try:
        limit = 50
        offset = (page - 1) * limit

        conditions = []
        params = []

        # mismatch filter
        if mismatch:
            conditions.append("b.match_result = 'mismatch'")

        # search filter
        if search:
            conditions.append("""
                (
                    CAST(b.saleguid AS TEXT) ILIKE %s
                    OR CAST(b.transactionid AS TEXT) ILIKE %s
                    OR b.propertyaddress ILIKE %s
                )
            """)
            search_term = f"%{search}%"
            params.extend([search_term, search_term, search_term])

        # track_status filter (NO parameter handling here anymore)
        if track_status:
            if track_status == "open":
                conditions.append("(t.track_status IS NULL OR t.track_status = 'open')")
            else:
                conditions.append("t.track_status = %s")
                params.append(track_status)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            {CLOSE_DATE_BASE_QUERY}

            SELECT
                b.saleguid,
                b.transactionid,
                b.propertyaddress,
                b.skyslope_close_date,
                b.be_close_date,
                b.match_result,
                COALESCE(t.track_status, 'open') AS status,
                t.assigned_to,
                t.notes,
                t.updated_at,
                t.updated_by
            FROM base b

            LEFT JOIN reconciliation_tracking t
                ON t.transaction_id = b.transactionid
                AND t.parameter = 'close_date'

            {where_clause}

            ORDER BY b.saleguid
            LIMIT %s OFFSET %s;
        """

        count_query = f"""
            {CLOSE_DATE_BASE_QUERY}

            SELECT COUNT(*) AS total_count
            FROM base b

            LEFT JOIN reconciliation_tracking t
                ON t.transaction_id = b.transactionid
                AND t.parameter = 'close_date'

            {where_clause};
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(count_query, params)
            total_count = cur.fetchone()["total_count"]

            cur.execute(query, params + [limit, offset])
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
