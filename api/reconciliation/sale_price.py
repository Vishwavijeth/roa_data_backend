from fastapi import APIRouter, Query
from db import get_conn
from psycopg2.extras import RealDictCursor

router = APIRouter()

SALE_PRICE_BASE_QUERY = """
WITH base AS (
    SELECT
        be.skyslopefileid AS skyslopefileid,
        s.saleguid,
        be.transaction_identifier_transactionid AS transactionid,
        be.property_address AS propertyaddress,

        be.transaction_status AS be_transaction_status,
        s.status AS skyslope_status,

        s.saleprice AS skyslope_sale_price,
        be.sale_price AS be_sale_price,

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

            WHEN s.saleprice IS DISTINCT FROM be.sale_price
                THEN 'mismatch'

            ELSE 'match'
        END AS match_result

    FROM brokerage_engine be
    LEFT JOIN sale s
        ON s.saleguid = be.skyslopefileid
)
"""

@router.get("/compare/sale_price/summary")
def sale_price_summary():
    conn = get_conn()

    try:
        query = f"""
            {SALE_PRICE_BASE_QUERY}

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


@router.get("/compare/sale_price")
def sale_price(
    page: int = Query(default=1, ge=1),
    mismatch: bool = Query(default=False),
    no_skyslope: bool = Query(default=False),
    search: str = Query(default=None)
):
    conn = get_conn()

    try:
        limit = 50
        offset = (page - 1) * limit

        conditions = []
        params = []

        if mismatch:
            conditions.append("match_result = 'mismatch'")

        if no_skyslope:
            conditions.append("match_result = 'no_skyslope_record'")

        if search:
            conditions.append("""
                (
                    CAST(saleguid AS TEXT) ILIKE %s
                    OR CAST(transactionid AS TEXT) ILIKE %s
                    OR propertyaddress ILIKE %s
                )
            """)
            search_term = f"%{search}%"
            params.extend([search_term, search_term, search_term])

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            {SALE_PRICE_BASE_QUERY}

            SELECT
                saleguid,
                transactionid,
                propertyaddress,
                skyslope_sale_price,
                be_sale_price,
                match_result
            FROM base
            {where_clause}
            ORDER BY saleguid
            LIMIT %s OFFSET %s;
        """

        count_query = f"""
            {SALE_PRICE_BASE_QUERY}

            SELECT COUNT(*) AS total_count
            FROM base
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