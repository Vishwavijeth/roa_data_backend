from typing import Optional, List
from fastapi import APIRouter, Query, Depends, HTTPException, Response
from psycopg2.extras import RealDictCursor
from db import get_db
from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter
import pandas as pd
import io
import datetime
from decimal import Decimal

router = APIRouter()

BASE_QUERY = """
WITH base_reconciliation AS (
    SELECT
        rd.transactionid,
        rd.be_source_table,
        rd.saleguid,
        rd.property_address,
        rd.be_close_date,
        rd.be_status,
        rd.be_transaction_specialist,
        rd.skyslope_reviewer,
        rd.gross_commission_match,
        rd.close_date_match,
        rd.status_match,
        rd.sale_price_match,
        rd.listing_price_match,
        rd.contract_date_match,
        rd.buyer_name_match,
        rd.seller_name_match,
        rd.buying_agent_match,
        rd.title_company_match
    FROM reconciliation_data rd
    WHERE rd.saleguid IS NOT NULL
),
latest_review AS (
    SELECT DISTINCT ON (rr.transactionid)
        rr.transactionid::uuid AS transactionid,
        rr.review_status,
        rr.notes,
        rr.updated_by,
        rr.updated_at
    FROM reconciliation_review rr
    ORDER BY rr.transactionid, rr.updated_at DESC
),
saleguid_group_flags AS (
    SELECT
        br.saleguid,
        ARRAY_AGG(DISTINCT LOWER(br.be_source_table)) AS linked_source_tables,
        BOOL_OR(LOWER(br.be_source_table) = 'sale income') AS has_sale_income,
        BOOL_OR(LOWER(br.be_source_table) = 'other income') AS has_other_income,
        BOOL_OR(br.gross_commission_match = 'mismatch') AS gross_commission_mismatch,
        BOOL_OR(br.close_date_match = 'mismatch') AS close_date_mismatch,
        BOOL_OR(br.status_match = 'mismatch') AS status_mismatch,
        BOOL_OR(br.sale_price_match = 'mismatch') AS sale_price_mismatch,
        BOOL_OR(br.listing_price_match = 'mismatch') AS listing_price_mismatch,
        BOOL_OR(br.contract_date_match = 'mismatch') AS contract_date_mismatch,
        BOOL_OR(br.buyer_name_match = 'mismatch') AS buyer_name_mismatch,
        BOOL_OR(br.seller_name_match = 'mismatch') AS seller_name_mismatch,
        BOOL_OR(br.buying_agent_match = 'mismatch') AS buying_agent_mismatch,
        BOOL_OR(br.title_company_match = 'mismatch') AS title_company_mismatch
    FROM base_reconciliation br
    GROUP BY br.saleguid
),
deduplicated_reconciliation AS (
    SELECT DISTINCT ON (br.saleguid)
        br.transactionid,
        br.be_source_table,
        br.saleguid,
        br.property_address,
        br.be_close_date,
        br.be_status,
        br.be_transaction_specialist,
        br.skyslope_reviewer
    FROM base_reconciliation br
    ORDER BY
        br.saleguid,
        CASE
            WHEN LOWER(br.be_source_table) = 'other income' THEN 0
            WHEN LOWER(br.be_source_table) = 'sale income' THEN 1
            ELSE 2
        END,
        br.transactionid
)
SELECT
    dr.transactionid,
    dr.be_source_table AS source_table,
    dr.saleguid,
    dr.property_address AS propertyaddress,
    dr.be_close_date,
    dr.be_status,
    dr.be_transaction_specialist,
    dr.skyslope_reviewer,
    sgf.linked_source_tables,
    sgf.has_sale_income,
    sgf.has_other_income,
    sgf.gross_commission_mismatch,
    sgf.close_date_mismatch,
    sgf.status_mismatch,
    sgf.sale_price_mismatch,
    sgf.listing_price_mismatch,
    sgf.contract_date_mismatch,
    sgf.buyer_name_mismatch,
    sgf.seller_name_mismatch,
    sgf.buying_agent_mismatch,
    sgf.title_company_mismatch,
    st.name AS skyslope_stage,
    lr.review_status,
    lr.notes AS review_notes,
    lr.updated_by AS review_updated_by,
    lr.updated_at AS review_updated_at,
    s.url AS skyslope_url
FROM deduplicated_reconciliation dr
JOIN saleguid_group_flags sgf
    ON sgf.saleguid = dr.saleguid
LEFT JOIN sale s
    ON s.saleguid = dr.saleguid
LEFT JOIN stage st
    ON st.stageid = s.stageid
LEFT JOIN latest_review lr
    ON lr.transactionid = dr.transactionid
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
    "gross_commission": "(cs.gross_commission_mismatch = TRUE)",
    "close_date": "(cs.close_date_mismatch = TRUE)",
    "status": "(cs.status_mismatch = TRUE)",
    "sale_price": "(cs.sale_price_mismatch = TRUE)",
    "listing_price": "(cs.listing_price_mismatch = TRUE)",
    "contract_date": "(cs.contract_date_mismatch = TRUE)",
    "buyer_name": "(cs.buyer_name_mismatch = TRUE)",
    "seller_name": "(cs.seller_name_mismatch = TRUE)",
    "buying_agent_name": "(cs.buying_agent_mismatch = TRUE)",
    "title_company": "(cs.title_company_mismatch = TRUE)",
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


def parse_text_list_params(values: Optional[List[str]]) -> List[str]:
    parsed = []
    if values:
        for value in values:
            for part in value.split(","):
                normalized = part.strip().lower()
                if normalized:
                    parsed.append(normalized)
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
    specialist: Optional[List[str]] = None,
    reviewer: Optional[List[str]] = None,
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
        source_table_conditions = []

        if "sale income" in parsed_source_tables:
            source_table_conditions.append("""
                (
                    cs.has_sale_income = TRUE
                    AND cs.has_other_income = FALSE
                )
            """)

        if "other income" in parsed_source_tables:
            source_table_conditions.append("""
                (
                    cs.has_other_income = TRUE
                )
            """)

        if source_table_conditions:
            conditions.append(f"({' OR '.join(source_table_conditions)})")

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

    parsed_specialists = parse_text_list_params(specialist)
    if parsed_specialists:
        if "unassigned" in parsed_specialists:
            non_unassigned_specialists = [s for s in parsed_specialists if s != "unassigned"]
            specialist_conditions = [
                "COALESCE(NULLIF(LOWER(TRIM(cs.be_transaction_specialist)), ''), 'unassigned') = 'unassigned'"
            ]
            if non_unassigned_specialists:
                specialist_conditions.append(
                    "LOWER(TRIM(cs.be_transaction_specialist)) = ANY(%s)"
                )
                params.append(non_unassigned_specialists)
            conditions.append(f"({' OR '.join(specialist_conditions)})")
        else:
            conditions.append("LOWER(TRIM(cs.be_transaction_specialist)) = ANY(%s)")
            params.append(parsed_specialists)

    parsed_reviewers = parse_text_list_params(reviewer)
    if parsed_reviewers:
        if "unassigned" in parsed_reviewers:
            non_unassigned_reviewers = [r for r in parsed_reviewers if r != "unassigned"]
            reviewer_conditions = [
                "COALESCE(NULLIF(LOWER(TRIM(cs.skyslope_reviewer)), ''), 'unassigned') = 'unassigned'"
            ]
            if non_unassigned_reviewers:
                reviewer_conditions.append(
                    "LOWER(TRIM(cs.skyslope_reviewer)) = ANY(%s)"
                )
                params.append(non_unassigned_reviewers)
            conditions.append(f"({' OR '.join(reviewer_conditions)})")
        else:
            conditions.append("LOWER(TRIM(cs.skyslope_reviewer)) = ANY(%s)")
            params.append(parsed_reviewers)

    if parsed_mismatch_params:
        active_filters = [
            MISMATCH_SQL_FILTERS[p]
            for p in parsed_mismatch_params
            if p in MISMATCH_SQL_FILTERS
        ]
        if active_filters:
            conditions.append(f"({' OR '.join(active_filters)})")

    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
    return where_clause, params


def get_mismatched_parameters_from_row(row):
    parameter_to_column = {
        "gross_commission": "gross_commission_mismatch",
        "close_date": "close_date_mismatch",
        "status": "status_mismatch",
        "sale_price": "sale_price_mismatch",
        "listing_price": "listing_price_mismatch",
        "contract_date": "contract_date_mismatch",
        "buyer_name": "buyer_name_mismatch",
        "seller_name": "seller_name_mismatch",
        "buying_agent_name": "buying_agent_mismatch",
        "title_company": "title_company_mismatch",
    }

    return [
        parameter
        for parameter, column_name in parameter_to_column.items()
        if row.get(column_name) is True
    ]


def get_summary_counts(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            WITH grouped_summary AS (
                SELECT DISTINCT ON (rd.saleguid)
                    rd.saleguid,
                    rd.be_source_table
                FROM reconciliation_data rd
                WHERE rd.saleguid IS NOT NULL
                ORDER BY
                    rd.saleguid,
                    CASE
                        WHEN LOWER(rd.be_source_table) = 'other income' THEN 0
                        WHEN LOWER(rd.be_source_table) = 'sale income' THEN 1
                        ELSE 2
                    END,
                    rd.transactionid
            )
            SELECT
                (SELECT COUNT(*) FROM grouped_summary) AS total_record,
                COUNT(*) FILTER (
                    WHERE LOWER(rd.be_source_table) = 'sale income' AND rd.saleguid IS NULL
                ) AS saleincome_no_skyslopefileid,
                COUNT(*) FILTER (
                    WHERE LOWER(rd.be_source_table) = 'other income' AND rd.saleguid IS NULL
                ) AS otherincome_no_skyslopefileid
            FROM reconciliation_data rd
        """)
        return cur.fetchone()


def get_status_filters(conn):
    query = """
        SELECT DISTINCT be_status AS status
        FROM reconciliation_data
        WHERE saleguid IS NOT NULL
          AND be_status IS NOT NULL
          AND TRIM(be_status) <> ''
        ORDER BY status
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query)
        rows = cur.fetchall()

    return [row["status"] for row in rows]


def get_specialist_filters(conn):
    query = """
        SELECT DISTINCT
            COALESCE(NULLIF(TRIM(be_transaction_specialist), ''), 'unassigned') AS specialist
        FROM reconciliation_data
        WHERE saleguid IS NOT NULL
        ORDER BY specialist
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query)
        rows = cur.fetchall()

    return [row["specialist"] for row in rows]


def get_reviewer_filters(conn):
    query = """
        SELECT DISTINCT
            COALESCE(NULLIF(TRIM(skyslope_reviewer), ''), 'unassigned') AS reviewer
        FROM reconciliation_data
        WHERE saleguid IS NOT NULL
        ORDER BY reviewer
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query)
        rows = cur.fetchall()

    return [row["reviewer"] for row in rows]


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
    specialist: Optional[List[str]] = Query(None),
    reviewer: Optional[List[str]] = Query(None),
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
        specialist=specialist,
        reviewer=reviewer,
    )

    data_query = f"""
        SELECT
            cs.transactionid,
            cs.saleguid,
            cs.skyslope_url,
            cs.propertyaddress,
            cs.linked_source_tables AS source_table,
            cs.skyslope_stage,
            cs.gross_commission_mismatch,
            cs.close_date_mismatch,
            cs.status_mismatch,
            cs.sale_price_mismatch,
            cs.listing_price_mismatch,
            cs.contract_date_mismatch,
            cs.buyer_name_mismatch,
            cs.seller_name_mismatch,
            cs.buying_agent_mismatch,
            cs.title_company_mismatch,
            cs.review_status,
            cs.review_notes,
            cs.review_updated_by,
            COUNT(*) OVER() AS _total_count
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
    specialist_filters = get_specialist_filters(conn)
    reviewer_filters = get_reviewer_filters(conn)

    results = []
    for row in rows:
        source_table_value = [
            SOURCE_TABLE_DISPLAY_NAMES.get((value or "").lower(), value)
            for value in (row.get("source_table") or [])
        ]

        results.append({
            "transactionid": str(row["transactionid"]) if row.get("transactionid") else None,
            "saleguid": str(row["saleguid"]) if row.get("saleguid") else None,
            "skyslope_url": row.get("skyslope_url"),
            "propertyaddress": row.get("propertyaddress"),
            "source_table": source_table_value,
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
            "specialist": specialist_filters,
            "reviewer": reviewer_filters,
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

@router.get("/recon-data/download")
def download_recon_data(conn=Depends(get_db)):
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    data_query = """
        SELECT
            rd.transactionid,
            rd.be_source_table,
            rd.saleguid,
            rd.property_address,
            rd.be_transaction_specialist,
            rd.skyslope_reviewer,

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

            rd.be_contract_date,
            rd.skyslope_contract_date,
            rd.contract_date_match,

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
        ORDER BY rd.transactionid
    """

    cursor.execute(data_query)
    data = cursor.fetchall()

    columns_map = {
        "transactionid": "Transaction ID",
        "be_source_table": "Source Table",
        "saleguid": "Sale GUID",
        "property_address": "Property Address",
        "be_transaction_specialist": "Transaction Specialist",
        "skyslope_reviewer": "Skyslope Reviewer",

        "be_gross_commission": "BE Gross Commission",
        "skyslope_gross_commission": "Skyslope Gross Commission",
        "gross_commission_match": "Gross Commission Match",

        "be_close_date_value": "BE Close Date",
        "skyslope_close_date_value": "Skyslope Close Date",
        "close_date_match": "Close Date Match",

        "be_status_value": "BE Status",
        "skyslope_status_value": "Skyslope Status",
        "status_match": "Status Match",

        "be_sale_price": "BE Sale Price",
        "skyslope_sale_price": "Skyslope Sale Price",
        "sale_price_match": "Sale Price Match",

        "be_listing_price": "BE Listing Price",
        "skyslope_listing_price": "Skyslope Listing Price",
        "listing_price_match": "Listing Price Match",

        "be_contract_date": "BE Contract Date",
        "skyslope_contract_date": "Skyslope Contract Date",
        "contract_date_match": "Contract Date Match",

        "be_buyer_name": "BE Buyer Name",
        "skyslope_buyer_name": "Skyslope Buyer Name",
        "buyer_name_match": "Buyer Name Match",

        "be_seller_name": "BE Seller Name",
        "skyslope_seller_name": "Skyslope Seller Name",
        "seller_name_match": "Seller Name Match",

        "be_buying_agent_name": "BE Buying Agent Name",
        "skyslope_buying_agent_name": "Skyslope Buying Agent Name",
        "buying_agent_match": "Buying Agent Match",

        "be_title_company": "BE Title Company",
        "skyslope_title_company": "Skyslope Title Company",
        "title_company_match": "Title Company Match",
    }

    rows_to_export = []
    for record in data:
        row_dict = {}
        for key, header in columns_map.items():
            val = record.get(key)

            if isinstance(val, Decimal):
                val = float(val)
            elif isinstance(val, (datetime.date, datetime.datetime)):
                val = val.strftime("%Y-%m-%d")
            elif isinstance(val, bool):
                val = "Yes" if val else "No"
            elif val is None:
                val = ""

            row_dict[header] = val

        rows_to_export.append(row_dict)

    df = pd.DataFrame(rows_to_export)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Recon Data", index=False)

        worksheet = writer.sheets["Recon Data"]

        for cell in worksheet[1]:
            cell.font = Font(bold=True)

        for col in worksheet.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)

            for cell in col:
                val = cell.value
                if val is not None:
                    max_len = max(max_len, len(str(val)))

            worksheet.column_dimensions[col_letter].width = max(max_len + 2, 12)

    output.seek(0)

    filename = "reconciliation_data_report.xlsx"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"'
    }

    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers
    )