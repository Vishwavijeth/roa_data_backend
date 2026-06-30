from typing import Optional, List
from fastapi import APIRouter, Query, Depends, HTTPException
from psycopg2.extras import RealDictCursor
from db import get_db
from services.recon_data_population import evaluate_row

router = APIRouter()

BASE_QUERY = """
WITH latest_review AS (
    SELECT DISTINCT ON (rr.transactionid)
        rr.transactionid::uuid AS transactionid,
        rr.review_status,
        rr.notes,
        rr.updated_by,
        rr.updated_at
    FROM reconciliation_review rr
    ORDER BY rr.transactionid, rr.updated_at DESC
)
SELECT
    rd.transactionid,
    rd.be_source_table AS source_table,
    rd.saleguid,
    rd.property_address AS propertyaddress,
    rd.be_close_date,
    rd.be_status,
    rd.gross_commission_match,
    rd.close_date_match,
    rd.status_match,
    rd.sale_price_match,
    rd.listing_price_match,
    rd.contract_date_match,
    rd.buyer_name_match,
    rd.seller_name_match,
    rd.buying_agent_match,
    rd.title_company_match,
    st.name AS skyslope_stage,
    lr.review_status,
    lr.notes AS review_notes,
    lr.updated_by AS review_updated_by,
    lr.updated_at AS review_updated_at,
    s.url AS skyslope_url
FROM reconciliation_data rd
LEFT JOIN sale s
    ON s.saleguid = rd.saleguid
LEFT JOIN stage st
    ON st.stageid = s.stageid
LEFT JOIN latest_review lr
    ON lr.transactionid = rd.transactionid
"""


PARAMETER_DISPLAY_NAMES = {
    "gross_commission": "Gross Commission",
    "close_date": "Close Date",
    "status": "Status",
    "sale_price": "Sale Price",
    "listing_price": "Listing Price",
    "contract_date": "Contract Date",
    "buyer_name": "Buyer Name",
    "seller_name": "Seller Name",
    "buying_agent_name": "Buying Agent Name",
    "title_company": "Title Company",
}


SOURCE_TABLE_DISPLAY_NAMES = {
    "sale income": "sale income",
    "other income": "other income",
}


SOURCE_TABLE_FILTER_MAP = {
    "sale income": "sale income",
    "other income": "other income",
}


MISMATCH_SQL_FILTERS = {
    "gross_commission": "(cs.gross_commission_match = 'mismatch')",
    "close_date": "(cs.close_date_match = 'mismatch')",
    "status": "(cs.status_match = 'mismatch')",
    "sale_price": "(cs.sale_price_match = 'mismatch')",
    "listing_price": "(cs.listing_price_match = 'mismatch')",
    "contract_date": "(cs.contract_date_match = 'mismatch')",
    "buyer_name": "(cs.buyer_name_match = 'mismatch')",
    "seller_name": "(cs.seller_name_match = 'mismatch')",
    "buying_agent_name": "(cs.buying_agent_match = 'mismatch')",
    "title_company": "(cs.title_company_match = 'mismatch')",
}


def parse_mismatch_params(mismatch_parameter: Optional[List[str]]) -> List[str]:
    parsed = []
    if mismatch_parameter:
        for p in mismatch_parameter:
            for part in p.split(","):
                normalized = part.strip().lower().replace(" ", "_")
                if normalized:
                    parsed.append(normalized)
    return parsed


def parse_source_table_params(source_table: Optional[List[str]]) -> List[str]:
    parsed = []
    if source_table:
        for value in source_table:
            for part in value.split(","):
                normalized = part.strip().lower()
                mapped_value = SOURCE_TABLE_FILTER_MAP.get(normalized)
                if mapped_value:
                    parsed.append(mapped_value)
    return parsed


def build_where_clause(
    search: Optional[str],
    parsed_mismatch_params: List[str],
    parsed_source_tables: Optional[List[str]] = None,
    from_close_date: Optional[str] = None,
    to_close_date: Optional[str] = None,
    status: Optional[List[str]] = None,
    skyslope_stage: Optional[List[str]] = None,
    review_status: Optional[List[str]] = None,
    saleincome_no_skyslopefileid: Optional[bool] = None,
    otherincome_no_skyslopefileid: Optional[bool] = None,
):
    conditions = []
    params = []

    if search:
        conditions.append("""
            (
                CAST(cs.transactionid AS TEXT) ILIKE %s
                OR cs.propertyaddress ILIKE %s
            )
        """)
        search_term = f"%{search}%"
        params.extend([search_term, search_term])

    if parsed_source_tables:
        conditions.append("LOWER(cs.source_table) = ANY(%s)")
        params.append(parsed_source_tables)

    if from_close_date:
        conditions.append("cs.be_close_date >= CAST(%s AS DATE)")
        params.append(from_close_date)

    if to_close_date:
        conditions.append("cs.be_close_date <= CAST(%s AS DATE)")
        params.append(to_close_date)

    if status:
        normalized_status = [s.strip().lower() for s in status if s and s.strip()]
        if normalized_status:
            conditions.append("LOWER(cs.be_status) = ANY(%s)")
            params.append(normalized_status)

    if skyslope_stage:
        normalized_stages = [s.strip().lower() for s in skyslope_stage if s and s.strip()]
        if normalized_stages:
            conditions.append("LOWER(cs.skyslope_stage) = ANY(%s)")
            params.append(normalized_stages)

    if review_status:
        normalized_review_filters = [s.strip().lower() for s in review_status if s and s.strip()]
        review_conditions = []

        if "in_review" in normalized_review_filters:
            review_conditions.append("LOWER(cs.review_status) = 'in_review'")

        if "review_done" in normalized_review_filters:
            review_conditions.append("LOWER(cs.review_status) = 'review_done'")

        if "not_a_mismatch" in normalized_review_filters:
            review_conditions.append("LOWER(cs.review_status) = 'not_a_mismatch'")

        if review_conditions:
            conditions.append(f"({' OR '.join(review_conditions)})")

    if parsed_mismatch_params:
        active_filters = [
            MISMATCH_SQL_FILTERS[p]
            for p in parsed_mismatch_params
            if p in MISMATCH_SQL_FILTERS
        ]
        if active_filters:
            conditions.append(f"({' OR '.join(active_filters)})")

    no_skyslopefileid_filters = []

    if saleincome_no_skyslopefileid is True:
        no_skyslopefileid_filters.append(
            "(LOWER(cs.source_table) = 'sale income' AND cs.saleguid IS NULL)"
        )

    if otherincome_no_skyslopefileid is True:
        no_skyslopefileid_filters.append(
            "(LOWER(cs.source_table) = 'other income' AND cs.saleguid IS NULL)"
        )

    if no_skyslopefileid_filters:
        conditions.append(f"({' OR '.join(no_skyslopefileid_filters)})")

    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
    return where_clause, params


def get_mismatched_parameters_from_row(row):
    parameter_to_column = {
        "gross_commission": "gross_commission_match",
        "close_date": "close_date_match",
        "status": "status_match",
        "sale_price": "sale_price_match",
        "listing_price": "listing_price_match",
        "contract_date": "contract_date_match",
        "buyer_name": "buyer_name_match",
        "seller_name": "seller_name_match",
        "buying_agent_name": "buying_agent_match",
        "title_company": "title_company_match",
    }

    return [
        parameter
        for parameter, column_name in parameter_to_column.items()
        if row.get(column_name) == "mismatch"
    ]


def get_summary_counts(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT
                COUNT(*) AS total_record,
                COUNT(*) FILTER (
                    WHERE LOWER(be_source_table) = 'sale income' AND saleguid IS NULL
                ) AS saleincome_no_skyslopefileid,
                COUNT(*) FILTER (
                    WHERE LOWER(be_source_table) = 'other income' AND saleguid IS NULL
                ) AS otherincome_no_skyslopefileid
            FROM reconciliation_data
        """)
        return cur.fetchone()


def get_status_filters(conn):
    query = """
        SELECT DISTINCT be_status AS status
        FROM reconciliation_data
        WHERE be_status IS NOT NULL
          AND TRIM(be_status) <> ''
        ORDER BY status
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query)
        rows = cur.fetchall()

    return [row["status"] for row in rows]


@router.get("/reconciliation/transactions")
def get_reconciliation_transactions(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1),
    search: Optional[str] = Query(default=None),
    mismatch_parameter: Optional[List[str]] = Query(default=None),
    source_table: Optional[List[str]] = Query(default=None),
    from_close_date: Optional[str] = Query(None),
    to_close_date: Optional[str] = Query(None),
    status: Optional[List[str]] = Query(None),
    skyslope_stage: Optional[List[str]] = Query(None),
    review_status: Optional[List[str]] = Query(None),
    saleincome_no_skyslopefileid: Optional[bool] = Query(None),
    otherincome_no_skyslopefileid: Optional[bool] = Query(None),
    conn=Depends(get_db),
):
    parsed_mismatch_params = parse_mismatch_params(mismatch_parameter)
    parsed_source_tables = parse_source_table_params(source_table)

    where_clause, params = build_where_clause(
        search=search,
        parsed_mismatch_params=parsed_mismatch_params,
        parsed_source_tables=parsed_source_tables,
        from_close_date=from_close_date,
        to_close_date=to_close_date,
        status=status,
        skyslope_stage=skyslope_stage,
        review_status=review_status,
        saleincome_no_skyslopefileid=saleincome_no_skyslopefileid,
        otherincome_no_skyslopefileid=otherincome_no_skyslopefileid,
    )

    data_query = f"""
        SELECT *, COUNT(*) OVER() AS _total_count
        FROM (
            {BASE_QUERY}
        ) cs
        {where_clause}
        ORDER BY cs.transactionid
        LIMIT %s OFFSET %s;
    """

    offset = (page - 1) * limit

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(data_query, params + [limit, offset])
        rows = cur.fetchall()

    total_count = rows[0]["_total_count"] if rows else 0
    summary = get_summary_counts(conn)
    status_filters = get_status_filters(conn)

    results = []
    for row in rows:
        source_table_label = SOURCE_TABLE_DISPLAY_NAMES.get(
            (row["source_table"] or "").lower(),
            row["source_table"]
        )

        results.append({
            "transactionid": str(row["transactionid"]) if row.get("transactionid") else None,
            "saleguid": str(row["saleguid"]) if row.get("saleguid") else None,
            "skyslope_url": row.get("skyslope_url"),
            "propertyaddress": row.get("propertyaddress"),
            "source_table": source_table_label,
            "skyslope_stage": row.get("skyslope_stage"),
            "mismatched_parameters": get_mismatched_parameters_from_row(row),
            "review": {
                "review_status": row.get("review_status"),
                "notes": row.get("review_notes"),
                "updated_by": row.get("review_updated_by"),
            },
        })

    return {
        "summary": {
            "total_record": summary["total_record"],
            "saleincome_no_skyslopefileid": summary["saleincome_no_skyslopefileid"],
            "otherincome_no_skyslopefileid": summary["otherincome_no_skyslopefileid"],
        },
        "count": total_count,
        "pagination": {
            "page": page,
            "limit": limit,
            "total_pages": (total_count + limit - 1) // limit if limit else 1,
        },
        "filters": {
            "parameter": list(PARAMETER_DISPLAY_NAMES.values()),
            "source_table": ["sale income", "other income"],
            "review_status": ["in_review", "review_done", "not_a_mismatch"],
            "status": status_filters,
        },
        "data": results,
    }

DETAIL_QUERY = """
SELECT
    rd.transactionid,
    rd.saleguid,
    rd.property_address AS propertyaddress,
    rd.be_source_table AS source_table,

    rd.be_status,
    rd.skyslope_status_value AS skyslope_status,

    rd.be_gross_commission,
    rd.skyslope_gross_commission,
    rd.gross_commission_match,

    rd.be_close_date_value,
    rd.skyslope_close_date_value,
    rd.close_date_match,

    rd.be_status_value,
    rd.skyslope_status_value,
    rd.status_match,

    rd.be_sale_price,
    rd.skyslope_sale_price,
    rd.sale_price_match,

    rd.be_listing_price,
    rd.skyslope_listing_price,
    rd.listing_price_match,

    rd.be_buyer_name,
    rd.skyslope_buyer_name,
    rd.buyer_name_match,

    rd.be_seller_name,
    rd.skyslope_seller_name,
    rd.seller_name_match,

    rd.be_buying_agent_name,
    rd.skyslope_buying_agent_name,
    rd.buying_agent_match,

    rd.be_title_company,
    rd.skyslope_title_company,
    rd.title_company_match
FROM reconciliation_data rd
WHERE rd.transactionid = %s
"""


@router.get("/reconciliation/transaction/{transaction_id}")
def get_reconciliation_transaction_details(
    transaction_id: str,
    conn=Depends(get_db),
):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(DETAIL_QUERY, (transaction_id,))
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    def serialize_date(value):
        return value.isoformat() if hasattr(value, "isoformat") else value

    def serialize_numeric(value):
        return float(value) if value is not None else None

    detailed_parameters = {
        "gross_commission": {
            "be_value": serialize_numeric(row.get("be_gross_commission")),
            "skyslope_value": serialize_numeric(row.get("skyslope_gross_commission")),
            "match_result": row.get("gross_commission_match"),
        },
        "close_date": {
            "be_value": serialize_date(row.get("be_close_date_value")),
            "skyslope_value": serialize_date(row.get("skyslope_close_date_value")),
            "match_result": row.get("close_date_match"),
        },
        "status": {
            "be_value": row.get("be_status_value"),
            "skyslope_value": row.get("skyslope_status_value"),
            "match_result": row.get("status_match"),
        },
        "sale_price": {
            "be_value": serialize_numeric(row.get("be_sale_price")),
            "skyslope_value": serialize_numeric(row.get("skyslope_sale_price")),
            "match_result": row.get("sale_price_match"),
        },
        "listing_price": {
            "be_value": serialize_numeric(row.get("be_listing_price")),
            "skyslope_value": serialize_numeric(row.get("skyslope_listing_price")),
            "match_result": row.get("listing_price_match"),
        },
        "buyer_name": {
            "be_value": row.get("be_buyer_name"),
            "skyslope_value": row.get("skyslope_buyer_name"),
            "match_result": row.get("buyer_name_match"),
        },
        "seller_name": {
            "be_value": row.get("be_seller_name"),
            "skyslope_value": row.get("skyslope_seller_name"),
            "match_result": row.get("seller_name_match"),
        },
        "buying_agent_name": {
            "be_value": row.get("be_buying_agent_name"),
            "skyslope_value": row.get("skyslope_buying_agent_name"),
            "match_result": row.get("buying_agent_match"),
        },
        "title_company": {
            "be_value": row.get("be_title_company"),
            "skyslope_value": row.get("skyslope_title_company"),
            "match_result": row.get("title_company_match"),
        },
    }

    return {
        "transactionid": str(row["transactionid"]) if row.get("transactionid") else None,
        "saleguid": str(row["saleguid"]) if row.get("saleguid") else None,
        "propertyaddress": row.get("propertyaddress"),
        "source_table": row.get("source_table"),
        "be_status": row.get("be_status"),
        "skyslope_status": row.get("skyslope_status"),
        "parameters": detailed_parameters,
    }