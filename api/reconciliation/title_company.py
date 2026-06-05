from fastapi import APIRouter, Query
from psycopg2.extras import RealDictCursor
from db import get_conn
from services.comparison import compare_names

router = APIRouter()

TITLE_COMPANY_BASE_QUERY = """
WITH base AS (
    SELECT
        be.skyslopefileid AS skyslopefileid,
        s.saleguid,
        be.transaction_identifier_transactionid AS transactionid,
        be.property_address AS propertyaddress,

        be.transaction_status AS be_transaction_status,
        s.status AS skyslope_status,

        COALESCE(
            (
                SELECT sc.company
                FROM sale_contact sc
                WHERE sc.saleguid = s.saleguid
                  AND LOWER(sc.role) = 'titlecompany'
                LIMIT 1
            ),
            ''
        ) AS skyslope_title_company,

        be.da_title_company AS be_title_company
    FROM brokerage_engine be
    LEFT JOIN sale s
        ON s.saleguid = be.skyslopefileid
)
"""


@router.get("/compare/title_company")
def compare_title_company(
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

        if search:
            conditions.append("""
                (
                    CAST(b.saleguid AS TEXT) ILIKE %s
                    OR CAST(b.transactionid AS TEXT) ILIKE %s
                    OR b.propertyaddress ILIKE %s
                    OR b.skyslope_title_company ILIKE %s
                    OR b.be_title_company ILIKE %s
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

        data_query = f"""
            {TITLE_COMPANY_BASE_QUERY}
            SELECT
                b.saleguid,
                b.transactionid,
                b.propertyaddress,
                b.be_transaction_status,
                b.skyslope_status,
                b.skyslope_title_company,
                b.be_title_company,
                t.track_status AS status,
                t.assigned_to,
                t.notes,
                t.updated_at,
                t.updated_by
            FROM base b
            LEFT JOIN reconciliation_tracking t
                ON t.transaction_id = b.transactionid
                AND t.parameter = 'title_company'
            {where_clause}
            ORDER BY b.saleguid;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(data_query, params)
            rows = cur.fetchall()

        results = []
        match_count = 0
        mismatch_count = 0
        no_skyslope_record_count = 0

        for row in rows:
            skyslope_title_company = row.get("skyslope_title_company")
            be_title_company = row.get("be_title_company")

            be_status = row.get("be_transaction_status")
            s_status = row.get("skyslope_status")

            if row.get("saleguid") is None:
                match_result = "no_skyslope_record"

            elif (
                be_status
                and be_status.lower() == "cancelled"
                and s_status
                and s_status.lower() in ["canceled/pend", "canceled/app"]
            ):
                match_result = None

            else:
                match_result = compare_names(
                    be_title_company,
                    skyslope_title_company
                )

            if match_result == "match":
                match_count += 1
            elif match_result == "mismatch":
                mismatch_count += 1
            elif match_result == "no_skyslope_record":
                no_skyslope_record_count += 1

            row_result = {
                "saleguid": row.get("saleguid"),
                "transactionid": row.get("transactionid"),
                "propertyaddress": row.get("propertyaddress"),
                "skyslope_title_company": skyslope_title_company,
                "be_title_company": be_title_company,
                "match_result": match_result,
                "status": row.get("status"),
                "assigned_to": row.get("assigned_to"),
                "notes": row.get("notes"),
                "updated_at": row.get("updated_at"),
                "updated_by": row.get("updated_by")
            }

            include_row = True
            if mismatch and match_result != "mismatch":
                include_row = False
            if no_skyslope and match_result != "no_skyslope_record":
                include_row = False

            if include_row:
                results.append(row_result)

        count = len(results)
        paginated_rows = results[offset:offset + limit]

        comparison_total = match_count + mismatch_count
        match_percentage = round((match_count / comparison_total) * 100, 2) if comparison_total else 0
        mismatch_percentage = round((mismatch_count / comparison_total) * 100, 2) if comparison_total else 0

        return {
            "summary": {
                "count": count,
                "match_percentage": match_percentage,
                "mismatch_percentage": mismatch_percentage,
                "mismatch_count": mismatch_count,
                "no_skyslope_record_count": no_skyslope_record_count
            },
            "page": page,
            "page_size": limit,
            "total_pages": (count + limit - 1) // limit,
            "data": paginated_rows
        }

    finally:
        conn.close()