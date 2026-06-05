from fastapi import APIRouter, Query, Depends
from psycopg2.extras import RealDictCursor
from db import get_db

router = APIRouter()

TRANSACTION_REVIEWER_MAPPING_BASE_QUERY = """
WITH base AS (
    SELECT
        be.skyslopefileid AS skyslopefileid,
        s.saleguid,
        be.transaction_identifier_transactionid AS transactionid,
        be.property_address AS propertyaddress,

        COALESCE(r.firstname || ' ' || r.lastname, NULL) AS skyslope_reviewer_name,
        be.transaction_specialist AS be_transaction_specialist,

        CASE
            WHEN s.saleguid IS NULL
                THEN 'no_skyslope_record'
            ELSE 'match'
        END AS match_result
    FROM brokerage_engine be
    LEFT JOIN sale s
        ON s.saleguid = be.skyslopefileid
    LEFT JOIN users r
        ON s.reviewerguid = r.userguid
)
"""


@router.get("/compare/transaction_reviewer_mapping")
def transaction_reviewer_mapping(
    page: int = Query(default=1, ge=1),
    no_skyslope: bool = Query(default=False),
    track_status: str = Query(default=None),
    search: str = Query(default=None),
    conn=Depends(get_db)
):
    limit = 50
    offset = (page - 1) * limit

    conditions = []
    params = []

    if no_skyslope:
        conditions.append("b.match_result = 'no_skyslope_record'")

    if search:
        conditions.append("""
            (
                CAST(b.saleguid AS TEXT) ILIKE %s
                OR CAST(b.transactionid AS TEXT) ILIKE %s
                OR b.propertyaddress ILIKE %s
                OR b.skyslope_reviewer_name ILIKE %s
                OR b.be_transaction_specialist ILIKE %s
            )
        """)
        search_term = f"%{search}%"
        params.extend([search_term, search_term, search_term, search_term, search_term])

    if track_status:
        if track_status == "open":
            conditions.append("(t.track_status IS NULL OR t.track_status = 'open')")
        else:
            conditions.append("t.track_status = %s")
            params.append(track_status)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    count_query = f"""
        {TRANSACTION_REVIEWER_MAPPING_BASE_QUERY}
        SELECT COUNT(*) AS count
        FROM base b
        LEFT JOIN reconciliation_tracking t
            ON t.transaction_id = b.transactionid
            AND t.parameter = 'transaction_reviewer_mapping'
        {where_clause};
    """

    data_query = f"""
        {TRANSACTION_REVIEWER_MAPPING_BASE_QUERY}
        SELECT
            b.saleguid,
            b.transactionid,
            b.propertyaddress,
            b.skyslope_reviewer_name,
            b.be_transaction_specialist,
            b.match_result,
            t.track_status AS status,
            t.assigned_to,
            t.notes,
            t.updated_at,
            t.updated_by
        FROM base b
        LEFT JOIN reconciliation_tracking t
            ON t.transaction_id = b.transactionid
            AND t.parameter = 'transaction_reviewer_mapping'
        {where_clause}
        ORDER BY b.saleguid
        LIMIT %s OFFSET %s;
    """

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(count_query, params)
        count = cur.fetchone()["count"]

        cur.execute(data_query, params + [limit, offset])
        rows = cur.fetchall()

    return {
        "summary": {
            "count": count,
            "no_skyslope_record_count": count if no_skyslope else None
        },
        "page": page,
        "page_size": limit,
        "total_pages": (count + limit - 1) // limit,
        "data": rows
    }