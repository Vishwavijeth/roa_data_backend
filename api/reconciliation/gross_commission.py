from fastapi import APIRouter, Query
from db import get_conn
from psycopg2.extras import RealDictCursor

router = APIRouter()

GROSS_COMMISSION_BASE_QUERY = """
WITH base AS (
    SELECT
        be.skyslopefileid,
        s.saleguid,

        be.transaction_identifier_transactionid AS transactionid,
        be.property_address AS propertyaddress,

        be.transaction_status AS be_transaction_status,
        s.status AS skyslope_status,

        scn.officeGrossCommissionOnSale AS skyslope_gross_commission,
        be.total_gross_commission AS be_gross_commission,

        CASE
            WHEN s.saleguid IS NULL
                THEN 'no_skyslope_record'

            WHEN be.transaction_status ILIKE 'cancelled'
                AND (
                    s.status ILIKE 'canceled/pend'
                    OR s.status ILIKE 'canceled/app'
                )
                THEN NULL

            WHEN scn.officeGrossCommissionOnSale IS NULL
                 OR be.total_gross_commission IS NULL
                 OR scn.officeGrossCommissionOnSale = 0
                 OR be.total_gross_commission = 0
                THEN NULL

            WHEN scn.officeGrossCommissionOnSale IS DISTINCT FROM be.total_gross_commission
                THEN 'mismatch'

            ELSE 'match'
        END AS match_result

    FROM brokerage_engine be

    LEFT JOIN sale s
        ON s.saleguid = be.skyslopefileid

    LEFT JOIN sale_commission scn
        ON scn.saleguid = be.skyslopefileid
)
"""

@router.get("/compare/gross_commission/summary")
def gross_commission_summary():
    conn = get_conn()

    try:
        query = f"""
            {GROSS_COMMISSION_BASE_QUERY}

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

@router.get("/compare/gross_commission")
def gross_commission(
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
            {GROSS_COMMISSION_BASE_QUERY}

            SELECT
                saleguid,
                transactionid,
                propertyaddress,
                skyslope_gross_commission,
                be_gross_commission,
                match_result
            FROM base
            {where_clause}
            ORDER BY saleguid
            LIMIT %s OFFSET %s;
        """

        count_query = f"""
            {GROSS_COMMISSION_BASE_QUERY}

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