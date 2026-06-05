from fastapi import APIRouter, Query, Depends
from psycopg2.extras import RealDictCursor
from db import get_db

router = APIRouter()

CONTRACT_DATE_BASE_QUERY = """
WITH base AS (
    SELECT
        be.skyslopefileid AS skyslopefileid,
        s.saleguid,

        be.transaction_identifier_transactionid AS transactionid,
        be.property_address AS propertyaddress,

        be.transaction_status AS be_transaction_status,
        s.status AS skyslope_status,

        s.contractacceptancedate AS skyslope_contract_date,
        be.contract_date AS be_contract_date,

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

            WHEN s.contractacceptancedate IS DISTINCT FROM be.contract_date
                THEN 'mismatch'

            ELSE 'match'
        END AS match_result

    FROM brokerage_engine be
    LEFT JOIN sale s
        ON s.saleguid = be.skyslopefileid
)
"""


@router.get("/compare/contract_date")
def contract_date(
    page: int = Query(default=1, ge=1),
    mismatch: bool = Query(default=False),
    no_skyslope: bool = Query(default=False),
    track_status: str = Query(default=None),
    search: str = Query(default=None),
    conn=Depends(get_db)
):
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
        {CONTRACT_DATE_BASE_QUERY}
        SELECT
            COUNT(*) FILTER (WHERE match_result = 'match') AS match_count,
            COUNT(*) FILTER (WHERE match_result = 'mismatch') AS mismatch_count,
            COUNT(*) FILTER (WHERE match_result = 'no_skyslope_record') AS no_skyslope_record_count
        FROM base;
    """

    count_query = f"""
        {CONTRACT_DATE_BASE_QUERY}
        SELECT COUNT(*) AS count
        FROM base b
        LEFT JOIN reconciliation_tracking t
            ON t.transaction_id = b.transactionid
            AND t.parameter = 'contract_date'
        {where_clause};
    """

    data_query = f"""
        {CONTRACT_DATE_BASE_QUERY}
        SELECT
            b.saleguid,
            b.transactionid,
            b.propertyaddress,
            b.skyslope_contract_date,
            b.be_contract_date,
            b.match_result,
            t.track_status AS status,
            t.assigned_to,
            t.notes,
            t.updated_at,
            t.updated_by
        FROM base b
        LEFT JOIN reconciliation_tracking t
            ON t.transaction_id = b.transactionid
            AND t.parameter = 'contract_date'
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
            "mismatch_count": mismatch_count,
            "no_skyslope_record_count": summary["no_skyslope_record_count"]
        },
        "page": page,
        "page_size": limit,
        "total_pages": (count + limit - 1) // limit,
        "data": rows
    }