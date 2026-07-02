from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Query, Depends
from psycopg2.extras import RealDictCursor
from db import get_db

router = APIRouter()


def build_analytics_where_clause(
    from_close_date: Optional[str] = None,
    to_close_date: Optional[str] = None,
    transaction_specialist: Optional[List[str]] = None,
    reviewer: Optional[List[str]] = None,
    status: Optional[List[str]] = None,
):
    conditions = []
    params = []

    if from_close_date:
        conditions.append("rd.be_close_date >= CAST(%s AS DATE)")
        params.append(from_close_date)

    if to_close_date:
        conditions.append("rd.be_close_date <= CAST(%s AS DATE)")
        params.append(to_close_date)

    if transaction_specialist:
        values = [s.strip().lower() for s in transaction_specialist if s and s.strip()]
        if values:
            conditions.append(
                "LOWER(COALESCE(NULLIF(TRIM(rd.be_transaction_specialist), ''), 'unassigned')) = ANY(%s)"
            )
            params.append(values)

    if reviewer:
        values = [r.strip().lower() for r in reviewer if r and r.strip()]
        if values:
            conditions.append(
                "LOWER(COALESCE(NULLIF(TRIM(rd.skyslope_reviewer), ''), 'unassigned')) = ANY(%s)"
            )
            params.append(values)

    if status:
        values = [s.strip().lower() for s in status if s and s.strip()]
        if values:
            conditions.append(
                "LOWER(COALESCE(NULLIF(TRIM(rd.be_status), ''), 'unassigned')) = ANY(%s)"
            )
            params.append(values)

    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
    return where_clause, params


@router.get("/reconciliation/analytics")
def get_reconciliation_analytics(
    from_close_date: Optional[str] = Query(None),
    to_close_date: Optional[str] = Query(None),
    transaction_specialist: Optional[List[str]] = Query(None),
    reviewer: Optional[List[str]] = Query(None),
    status: Optional[List[str]] = Query(None),
    conn=Depends(get_db),
):
    where_clause, params = build_analytics_where_clause(
        from_close_date=from_close_date,
        to_close_date=to_close_date,
        transaction_specialist=transaction_specialist,
        reviewer=reviewer,
        status=status,
    )

    base_cte = f"""
        WITH filtered_data AS (
            SELECT
                rd.transactionid,
                rd.be_source_table,
                COALESCE(NULLIF(TRIM(rd.be_status), ''), 'Unassigned') AS be_status,
                (
                    CASE WHEN rd.gross_commission_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN rd.close_date_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN rd.status_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN rd.sale_price_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN rd.listing_price_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN rd.contract_date_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN rd.buyer_name_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN rd.seller_name_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN rd.buying_agent_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN rd.title_company_match = 'mismatch' THEN 1 ELSE 0 END
                ) AS mismatch_parameter_count,
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
            {where_clause}
        )
    """

    overview_query = base_cte + """
        SELECT
            COUNT(*) AS total_records,
            COUNT(*) FILTER (
                WHERE LOWER(be_source_table) = 'sale income'
            ) AS total_sale_income,
            COUNT(*) FILTER (
                WHERE LOWER(be_source_table) = 'other income'
            ) AS total_other_income,
            COUNT(*) FILTER (
                WHERE mismatch_parameter_count >= 1
            ) AS mismatched_transactions
        FROM filtered_data
    """

    status_distribution_query = base_cte + """
        SELECT
            LOWER(be_status) AS status,
            COUNT(*) AS count
        FROM filtered_data
        WHERE mismatch_parameter_count >= 1
        GROUP BY LOWER(be_status)
        ORDER BY count DESC, status ASC
    """

    parameter_breakdown_query = base_cte + """
        SELECT
            COUNT(*) FILTER (
                WHERE gross_commission_match = 'mismatch'
            ) AS gross_commission,
            COUNT(*) FILTER (
                WHERE close_date_match = 'mismatch'
            ) AS close_date,
            COUNT(*) FILTER (
                WHERE status_match = 'mismatch'
            ) AS status,
            COUNT(*) FILTER (
                WHERE sale_price_match = 'mismatch'
            ) AS sale_price,
            COUNT(*) FILTER (
                WHERE listing_price_match = 'mismatch'
            ) AS listing_price,
            COUNT(*) FILTER (
                WHERE contract_date_match = 'mismatch'
            ) AS contract_date,
            COUNT(*) FILTER (
                WHERE buyer_name_match = 'mismatch'
            ) AS buyer_name,
            COUNT(*) FILTER (
                WHERE seller_name_match = 'mismatch'
            ) AS seller_name,
            COUNT(*) FILTER (
                WHERE buying_agent_match = 'mismatch'
            ) AS buying_agent_name,
            COUNT(*) FILTER (
                WHERE title_company_match = 'mismatch'
            ) AS title_company
        FROM filtered_data
        WHERE mismatch_parameter_count >= 1
    """

    specialist_filters_query = """
        SELECT DISTINCT
            COALESCE(NULLIF(TRIM(be_transaction_specialist), ''), 'unassigned') AS transaction_specialist
        FROM reconciliation_data
        ORDER BY transaction_specialist
    """

    reviewer_filters_query = """
        SELECT DISTINCT
            COALESCE(NULLIF(TRIM(skyslope_reviewer), ''), 'unassigned') AS reviewer
        FROM reconciliation_data
        ORDER BY reviewer
    """

    status_filters_query = """
        SELECT DISTINCT
            COALESCE(NULLIF(TRIM(be_status), ''), 'unassigned') AS status
        FROM reconciliation_data
        ORDER BY status
    """

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(overview_query, params)
        overview_row = cur.fetchone()

        cur.execute(status_distribution_query, params)
        status_distribution_rows = cur.fetchall()

        cur.execute(parameter_breakdown_query, params)
        parameter_row = cur.fetchone()

        cur.execute(specialist_filters_query)
        specialist_filters_rows = cur.fetchall()

        cur.execute(reviewer_filters_query)
        reviewer_filters_rows = cur.fetchall()

        cur.execute(status_filters_query)
        status_filters_rows = cur.fetchall()

    specialist_filters = [row["transaction_specialist"] for row in specialist_filters_rows]
    reviewer_filters = [row["reviewer"] for row in reviewer_filters_rows]
    status_filters = [row["status"] for row in status_filters_rows]

    status_distribution = [
        {
            "status": row["status"],
            "count": row["count"] or 0,
        }
        for row in status_distribution_rows
    ]

    parameter_breakdown = {
        "gross_commission": parameter_row["gross_commission"] or 0,
        "close_date": parameter_row["close_date"] or 0,
        "status": parameter_row["status"] or 0,
        "sale_price": parameter_row["sale_price"] or 0,
        "listing_price": parameter_row["listing_price"] or 0,
        "contract_date": parameter_row["contract_date"] or 0,
        "buyer_name": parameter_row["buyer_name"] or 0,
        "seller_name": parameter_row["seller_name"] or 0,
        "buying_agent_name": parameter_row["buying_agent_name"] or 0,
        "title_company": parameter_row["title_company"] or 0,
    }

    result: Dict[str, Any] = {
        "filters": {
            "transaction_specialist": specialist_filters,
            "reviewer": reviewer_filters,
            "status": status_filters,
        },
        "overview": {
            "total_records": overview_row["total_records"] or 0,
            "total_sale_income": overview_row["total_sale_income"] or 0,
            "total_other_income": overview_row["total_other_income"] or 0,
            "mismatched_transactions": overview_row["mismatched_transactions"] or 0,
        },
        "status_distribution": status_distribution,
        "parameter_breakdown": {
            "note": "A transaction can appear in multiple parameter categories.",
            "data": parameter_breakdown,
        },
    }

    return result