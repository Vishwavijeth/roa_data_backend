from fastapi import APIRouter, Query, Depends
from db import get_db
from psycopg2.extras import RealDictCursor

router = APIRouter()

STATUS_BASE_QUERY = """
WITH brokerage_base AS (
    SELECT
        'brokerage_engine'::text AS source_table,
        be.skyslopefileid::text AS skyslopefileid,
        be.transaction_identifier_transactionid AS transactionid,
        be.property_address::text AS propertyaddress,
        be.transaction_status::text AS source_status
    FROM brokerage_engine be
),
other_income_base AS (
    SELECT
        'otherincome_transactions'::text AS source_table,
        oit.skyslopefileid::text AS skyslopefileid,
        oit.transaction_identifier_transactionid AS transactionid,
        oit.property_address::text AS propertyaddress,
        oit.transaction_status::text AS source_status
    FROM otherincome_transactions oit
),
combined_source AS (
    SELECT
        source_table,
        skyslopefileid,
        transactionid,
        propertyaddress,
        source_status
    FROM brokerage_base

    UNION ALL

    SELECT
        source_table,
        skyslopefileid,
        transactionid,
        propertyaddress,
        source_status
    FROM other_income_base
),
base AS (
    SELECT
        cs.source_table,
        cs.skyslopefileid,
        s.saleguid,
        cs.transactionid,
        cs.propertyaddress,
        cs.source_status,
        s.status AS skyslope_status,
        CASE
            WHEN s.saleguid IS NULL
                THEN 'no_skyslope_record'

            WHEN LOWER(cs.source_status) = 'cancelled'
                AND LOWER(COALESCE(s.status, '')) IN ('canceled/app', 'canceled/pend')
                THEN 'match'

            WHEN LOWER(cs.source_status) = 'closed'
                AND LOWER(COALESCE(s.status, '')) IN ('archived', 'closed')
                THEN 'match'

            WHEN LOWER(cs.source_status) = LOWER(COALESCE(s.status, ''))
                THEN 'match'

            WHEN LOWER(cs.source_status) = 'pending'
                AND LOWER(COALESCE(s.status, '')) = 'expired'
                THEN NULL

            ELSE 'mismatch'
        END AS match_result
    FROM combined_source cs
    LEFT JOIN sale s
        ON s.saleguid::text = cs.skyslopefileid
),
missing_skyslopefileid_base AS (
    SELECT
        'brokerage_engine'::text AS source_table,
        NULL::text AS saleguid,
        be.transaction_identifier_transactionid AS transactionid,
        be.property_address::text AS propertyaddress,
        be.transaction_status::text AS be_status,
        NULL::text AS skyslope_status,
        'no_skyslope_record'::text AS match_result
    FROM brokerage_engine be
    WHERE be.skyslopefileid IS NULL

    UNION ALL

    SELECT
        'otherincome_transactions'::text AS source_table,
        NULL::text AS saleguid,
        oit.transaction_identifier_transactionid AS transactionid,
        oit.property_address::text AS propertyaddress,
        oit.transaction_status::text AS be_status,
        NULL::text AS skyslope_status,
        'no_skyslope_record'::text AS match_result
    FROM otherincome_transactions oit
    WHERE oit.skyslopefileid IS NULL
)
"""


@router.get("/compare/status")
def status(
    page: int = Query(default=1, ge=1),
    mismatch: bool = Query(default=False),
    no_skyslope: bool = Query(default=False),
    saleincome_no_skyslopefileid: bool = Query(default=False),
    otherincome_no_skyslopefileid: bool = Query(default=False),
    track_status: str = Query(default=None),
    search: str = Query(default=None),
    conn=Depends(get_db)
):
    limit = 50
    offset = (page - 1) * limit

    missing_mode = saleincome_no_skyslopefileid or otherincome_no_skyslopefileid

    summary_query = f"""
        {STATUS_BASE_QUERY}
        SELECT
            COUNT(*) FILTER (WHERE match_result = 'match') AS match_count,
            COUNT(*) FILTER (WHERE match_result = 'mismatch') AS mismatch_count,
            (
                SELECT COUNT(*)
                FROM brokerage_engine be
                WHERE be.skyslopefileid IS NULL
            ) AS saleincome_no_skyslopefileid_count,
            (
                SELECT COUNT(*)
                FROM otherincome_transactions oit
                WHERE oit.skyslopefileid IS NULL
            ) AS otherincome_no_skyslopefileid_count
        FROM base;
    """

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(summary_query)
        summary = cur.fetchone()

        if missing_mode:
            conditions = []
            params = []

            if saleincome_no_skyslopefileid and not otherincome_no_skyslopefileid:
                conditions.append("m.source_table = 'brokerage_engine'")

            if otherincome_no_skyslopefileid and not saleincome_no_skyslopefileid:
                conditions.append("m.source_table = 'otherincome_transactions'")

            if search:
                conditions.append("m.propertyaddress ILIKE %s")
                search_term = f"%{search}%"
                params.append(search_term)

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            count_query = f"""
                {STATUS_BASE_QUERY}
                SELECT COUNT(*) AS count
                FROM missing_skyslopefileid_base m
                {where_clause};
            """

            data_query = f"""
                {STATUS_BASE_QUERY}
                SELECT
                    m.source_table,
                    m.saleguid,
                    m.transactionid,
                    m.propertyaddress,
                    m.be_status,
                    m.skyslope_status,
                    m.match_result,
                    NULL::text AS status,
                    NULL::text AS assigned_to,
                    NULL::text AS notes,
                    NULL::timestamp AS updated_at,
                    NULL::text AS updated_by
                FROM missing_skyslopefileid_base m
                {where_clause}
                ORDER BY m.transactionid
                LIMIT %s OFFSET %s;
            """

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
                    "saleincome_no_skyslopefileid_count": summary["saleincome_no_skyslopefileid_count"],
                    "otherincome_no_skyslopefileid_count": summary["otherincome_no_skyslopefileid_count"]
                },
                "page": page,
                "page_size": limit,
                "total_pages": (count + limit - 1) // limit,
                "data": rows
            }

        conditions = []
        params = []

        if mismatch:
            conditions.append("b.match_result = 'mismatch'")

        if no_skyslope:
            conditions.append("b.match_result = 'no_skyslope_record'")

        if search:
            conditions.append("b.propertyaddress ILIKE %s")
            search_term = f"%{search}%"
            params.append(search_term)

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
            {STATUS_BASE_QUERY}
            SELECT COUNT(*) AS count
            FROM base b
            LEFT JOIN reconciliation_tracking t
                ON t.transaction_id = b.transactionid
                AND t.parameter = 'status'
            {where_clause};
        """

        data_query = f"""
            {STATUS_BASE_QUERY}
            SELECT
                b.source_table,
                b.saleguid,
                b.transactionid,
                b.propertyaddress,
                b.source_status AS be_status,
                b.skyslope_status,
                b.match_result,
                t.track_status AS status,
                t.assigned_to,
                t.notes,
                t.updated_at,
                t.updated_by
            FROM base b
            LEFT JOIN reconciliation_tracking t
                ON t.transaction_id = b.transactionid
                AND t.parameter = 'status'
            {where_clause}
            ORDER BY b.saleguid
            LIMIT %s OFFSET %s;
        """

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
            "saleincome_no_skyslopefileid_count": summary["saleincome_no_skyslopefileid_count"],
            "otherincome_no_skyslopefileid_count": summary["otherincome_no_skyslopefileid_count"]
        },
        "page": page,
        "page_size": limit,
        "total_pages": (count + limit - 1) // limit,
        "data": rows
    }