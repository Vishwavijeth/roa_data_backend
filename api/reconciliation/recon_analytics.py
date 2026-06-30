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
                "LOWER(COALESCE(NULLIF(TRIM(rd.be_transaction_specialist), ''), 'Unassigned')) = ANY(%s)"
            )
            params.append(values)

    if reviewer:
        values = [r.strip().lower() for r in reviewer if r and r.strip()]
        if values:
            conditions.append(
                "LOWER(COALESCE(NULLIF(TRIM(rd.skyslope_reviewer), ''), 'Unassigned')) = ANY(%s)"
            )
            params.append(values)

    if status:
        values = [s.strip().lower() for s in status if s and s.strip()]
        if values:
            conditions.append(
                "LOWER(COALESCE(NULLIF(TRIM(rd.be_status), ''), 'Unassigned')) = ANY(%s)"
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

    analytics_query = f"""
        WITH filtered_data AS (
            SELECT *
            FROM reconciliation_data rd
            {where_clause}
        ),
        mismatch_counts AS (
            SELECT
                *,
                (
                    CASE WHEN gross_commission_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN close_date_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN status_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN sale_price_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN listing_price_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN buyer_name_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN seller_name_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN buying_agent_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN title_company_match = 'mismatch' THEN 1 ELSE 0 END
                ) AS mismatch_parameter_count
            FROM filtered_data
        )
        SELECT
            COUNT(*) AS total_records,

            COUNT(*) FILTER (
                WHERE be_source_table = 'sale income'
            ) AS total_sale_income,

            COUNT(*) FILTER (
                WHERE be_source_table = 'other income'
            ) AS total_other_income,

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
            ) AS title_company,

            COUNT(*) FILTER (
                WHERE mismatch_parameter_count > 2
            ) AS transactions_with_more_than_2_mismatches
        FROM mismatch_counts
    """

    specialist_query = f"""
        WITH filtered_data AS (
            SELECT *
            FROM reconciliation_data rd
            {where_clause}
        ),
        mismatch_counts AS (
            SELECT
                COALESCE(NULLIF(TRIM(be_transaction_specialist), ''), 'Unassigned') AS specialist_name,
                (
                    CASE WHEN gross_commission_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN close_date_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN status_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN sale_price_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN listing_price_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN buyer_name_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN seller_name_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN buying_agent_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN title_company_match = 'mismatch' THEN 1 ELSE 0 END
                ) AS mismatch_parameter_count
            FROM filtered_data
        )
        SELECT
            specialist_name,
            COUNT(*) FILTER (WHERE mismatch_parameter_count >= 1) AS mismatch_transactions,
            COUNT(*) FILTER (WHERE mismatch_parameter_count > 2) AS mismatch_transactions_gt_2
        FROM mismatch_counts
        GROUP BY specialist_name
        ORDER BY mismatch_transactions DESC, specialist_name ASC
        LIMIT 1
    """

    reviewer_query = f"""
        WITH filtered_data AS (
            SELECT *
            FROM reconciliation_data rd
            {where_clause}
        ),
        mismatch_counts AS (
            SELECT
                COALESCE(NULLIF(TRIM(skyslope_reviewer), ''), 'Unassigned') AS reviewer_name,
                (
                    CASE WHEN gross_commission_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN close_date_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN status_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN sale_price_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN listing_price_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN buyer_name_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN seller_name_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN buying_agent_match = 'mismatch' THEN 1 ELSE 0 END +
                    CASE WHEN title_company_match = 'mismatch' THEN 1 ELSE 0 END
                ) AS mismatch_parameter_count
            FROM filtered_data
        )
        SELECT
            reviewer_name,
            COUNT(*) FILTER (WHERE mismatch_parameter_count >= 1) AS mismatch_transactions,
            COUNT(*) FILTER (WHERE mismatch_parameter_count > 2) AS mismatch_transactions_gt_2
        FROM mismatch_counts
        GROUP BY reviewer_name
        ORDER BY mismatch_transactions DESC, reviewer_name ASC
        LIMIT 1
    """

    specialist_filters_query = """
        SELECT DISTINCT
            COALESCE(NULLIF(TRIM(be_transaction_specialist), ''), 'Unassigned') AS transaction_specialist
        FROM reconciliation_data
        ORDER BY transaction_specialist
    """

    reviewer_filters_query = """
        SELECT DISTINCT
            COALESCE(NULLIF(TRIM(skyslope_reviewer), ''), 'Unassigned') AS reviewer
        FROM reconciliation_data
        ORDER BY reviewer
    """

    status_filters_query = """
        SELECT DISTINCT
            COALESCE(NULLIF(TRIM(be_status), ''), 'Unassigned') AS status
        FROM reconciliation_data
        ORDER BY status
    """

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(analytics_query, params)
        main_row = cur.fetchone()

        cur.execute(specialist_query, params)
        specialist_row = cur.fetchone()

        cur.execute(reviewer_query, params)
        reviewer_row = cur.fetchone()

        cur.execute(specialist_filters_query)
        specialist_filters_rows = cur.fetchall()

        cur.execute(reviewer_filters_query)
        reviewer_filters_rows = cur.fetchall()

        cur.execute(status_filters_query)
        status_filters_rows = cur.fetchall()

    specialist_filters = [row["transaction_specialist"] for row in specialist_filters_rows]
    reviewer_filters = [row["reviewer"] for row in reviewer_filters_rows]
    status_filters = [row["status"] for row in status_filters_rows]

    result: Dict[str, Any] = {
        "filters": {
            "transaction_specialist": specialist_filters,
            "reviewer": reviewer_filters,
            "status": status_filters,
        },
        "data": {
            "total_records": main_row["total_records"] or 0,
            "total_sale_income": main_row["total_sale_income"] or 0,
            "total_other_income": main_row["total_other_income"] or 0,

            "gross_commission": main_row["gross_commission"] or 0,
            "close_date": main_row["close_date"] or 0,
            "status": main_row["status"] or 0,
            "sale_price": main_row["sale_price"] or 0,
            "listing_price": main_row["listing_price"] or 0,
            "buyer_name": main_row["buyer_name"] or 0,
            "seller_name": main_row["seller_name"] or 0,
            "buying_agent_name": main_row["buying_agent_name"] or 0,
            "title_company": main_row["title_company"] or 0,
            "transactions_with_more_than_2_mismatches": main_row["transactions_with_more_than_2_mismatches"] or 0,

            "top_transaction_specialist": {
                "name": specialist_row["specialist_name"] if specialist_row else "Unassigned",
                "mismatch_transactions": specialist_row["mismatch_transactions"] if specialist_row else 0,
                "mismatch_transactions_gt_2": specialist_row["mismatch_transactions_gt_2"] if specialist_row else 0,
            },

            "top_reviewer": {
                "name": reviewer_row["reviewer_name"] if reviewer_row else "Unassigned",
                "mismatch_transactions": reviewer_row["mismatch_transactions"] if reviewer_row else 0,
                "mismatch_transactions_gt_2": reviewer_row["mismatch_transactions_gt_2"] if reviewer_row else 0,
            },
        },
    }

    return result