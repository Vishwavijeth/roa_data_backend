from fastapi import APIRouter, Query
from db import get_conn
from psycopg2.extras import RealDictCursor

router = APIRouter()

GROSS_COMMISSION_BASE_QUERY = """
WITH commission_resolved AS (
    SELECT
        be.skyslopefileid,
        be.transaction_identifier_transactionid,
        be.property_address,
        be.transaction_status,
        be.tags,
        CASE
            WHEN be.tags ILIKE '%%listingside%%' AND be.tags ILIKE '%%sellingside%%'
                THEN be.total_gross_commission
            WHEN be.tags ILIKE '%%listingside%%'
                THEN be.listing_side_gross_commission
            WHEN be.tags ILIKE '%%sellingside%%'
                THEN be.buying_side_gross_commission
            ELSE be.buying_side_gross_commission
        END AS be_gross_commission
    FROM brokerage_engine be
),
base AS (
    SELECT
        be.skyslopefileid,
        s.saleguid,
        be.transaction_identifier_transactionid AS transactionid,
        be.property_address AS propertyaddress,
        be.transaction_status AS be_transaction_status,
        s.status AS skyslope_status,
        CASE
            WHEN be.tags ILIKE '%%listingside%%' AND be.tags ILIKE '%%sellingside%%'
                THEN scn.officeGrossCommissionOnSale
            WHEN be.tags ILIKE '%%listingside%%'
                THEN COALESCE(scn.listingcommissionamount, scn.officeGrossCommissionOnSale)
            WHEN be.tags ILIKE '%%sellingside%%'
                THEN COALESCE(scn.salecommissionamount, scn.officeGrossCommissionOnSale)
            ELSE COALESCE(scn.salecommissionamount, scn.officeGrossCommissionOnSale)
        END AS skyslope_gross_commission,
        scn.officeGrossCommissionOnSale,
        scn.listingcommissionamount,
        scn.salecommissionamount,
        be.be_gross_commission,
        CASE
            WHEN s.saleguid IS NULL
                THEN 'no_skyslope_record'

            WHEN LOWER(be.transaction_status) = 'cancelled'
                AND LOWER(s.status) IN ('canceled/pend', 'canceled/app')
                THEN NULL

            WHEN be.tags ILIKE '%%listingside%%' AND be.tags ILIKE '%%sellingside%%'
                THEN CASE
                    WHEN scn.officeGrossCommissionOnSale IS NULL
                      OR be.be_gross_commission IS NULL
                      OR scn.officeGrossCommissionOnSale = 0
                      OR be.be_gross_commission = 0
                    THEN NULL
                    WHEN ROUND(scn.officeGrossCommissionOnSale::numeric, 2)
                         IS DISTINCT FROM ROUND(be.be_gross_commission::numeric, 2)
                    THEN 'mismatch'
                    ELSE 'match'
                END

            WHEN be.tags ILIKE '%%listingside%%'
                THEN CASE
                    WHEN COALESCE(scn.listingcommissionamount, scn.officeGrossCommissionOnSale) IS NULL
                      OR be.be_gross_commission IS NULL
                      OR COALESCE(scn.listingcommissionamount, scn.officeGrossCommissionOnSale) = 0
                      OR be.be_gross_commission = 0
                    THEN NULL
                    WHEN ROUND(COALESCE(scn.listingcommissionamount, scn.officeGrossCommissionOnSale)::numeric, 2)
                         IS DISTINCT FROM ROUND(be.be_gross_commission::numeric, 2)
                    THEN 'mismatch'
                    ELSE 'match'
                END

            WHEN be.tags ILIKE '%%sellingside%%'
                THEN CASE
                    WHEN COALESCE(scn.salecommissionamount, scn.officeGrossCommissionOnSale) IS NULL
                      OR be.be_gross_commission IS NULL
                      OR COALESCE(scn.salecommissionamount, scn.officeGrossCommissionOnSale) = 0
                      OR be.be_gross_commission = 0
                    THEN NULL
                    WHEN ROUND(COALESCE(scn.salecommissionamount, scn.officeGrossCommissionOnSale)::numeric, 2)
                         IS DISTINCT FROM ROUND(be.be_gross_commission::numeric, 2)
                    THEN 'mismatch'
                    ELSE 'match'
                END

            ELSE NULL
        END AS match_result
    FROM commission_resolved be
    LEFT JOIN sale s
        ON s.saleguid = be.skyslopefileid
    LEFT JOIN sale_commission scn
        ON scn.saleguid = s.saleguid
)
"""

@router.get("/compare/gross_commission")
def gross_commission(
    page: int = Query(default=1, ge=1),
    mismatch: bool = Query(default=False),
    no_skyslope: bool = Query(default=False),
    track_status: str = Query(default=None),
    search: str = Query(default=None)
):
    conn = get_conn()

    try:
        limit = 50
        offset = (page - 1) * limit

        conditions = []
        params = []

        if mismatch:
            conditions.append("b.match_result = 'mismatch'")

        if no_skyslope:
            conditions.append("b.match_result = 'no_skyslope_record'")

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

        if track_status:
            if track_status == "open":
                conditions.append("(t.track_status IS NULL OR t.track_status = 'open')")
            else:
                conditions.append("t.track_status = %s")
                params.append(track_status)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        summary_query = f"""
            {GROSS_COMMISSION_BASE_QUERY}
            SELECT
                COUNT(*) FILTER (WHERE match_result = 'match') AS match_count,
                COUNT(*) FILTER (WHERE match_result = 'mismatch') AS mismatch_count,
                COUNT(*) FILTER (WHERE match_result = 'no_skyslope_record') AS no_skyslope_record_count
            FROM base;
        """

        count_query = f"""
            {GROSS_COMMISSION_BASE_QUERY}
            SELECT COUNT(*) AS count
            FROM base b
            LEFT JOIN reconciliation_tracking t
                ON t.transaction_id = b.transactionid
                AND t.parameter = 'gross_commission'
            {where_clause};
        """

        data_query = f"""
            {GROSS_COMMISSION_BASE_QUERY}
            SELECT
                b.saleguid,
                b.transactionid,
                b.propertyaddress,
                b.skyslope_gross_commission,
                b.be_gross_commission,
                b.match_result,
                t.track_status AS status,
                t.assigned_to,
                t.notes,
                t.updated_at,
                t.updated_by
            FROM base b
            LEFT JOIN reconciliation_tracking t
                ON t.transaction_id = b.transactionid
                AND t.parameter = 'gross_commission'
            {where_clause}
            ORDER BY b.saleguid
            LIMIT %s OFFSET %s;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(summary_query)
            summary = cur.fetchone()

            cur.execute(count_query, params)
            count = cur.fetchone()["count"]

            cur.execute(data_query, params + [limit, offset])
            rows = cur.fetchall()

        match_count = summary["match_count"] or 0
        mismatch_count = summary["mismatch_count"] or 0
        comparison_total = match_count + mismatch_count

        match_percentage = round((match_count / comparison_total) * 100, 2) if comparison_total else 0
        mismatch_percentage = round((mismatch_count / comparison_total) * 100, 2) if comparison_total else 0

        return {
            "summary": {
                "count": count,
                "match_percentage": match_percentage,
                "mismatch_percentage": mismatch_percentage,
                "no_skyslope_record_count": summary["no_skyslope_record_count"]
            },
            "data": rows
        }

    finally:
        conn.close()