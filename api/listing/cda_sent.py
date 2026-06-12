from fastapi import APIRouter, Query, Response, Depends
from db import get_conn, get_db
from psycopg2.extras import RealDictCursor
from services.comparison import compare_names, compare_listing_price
import io
import pandas as pd
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from decimal import Decimal
import datetime

router = APIRouter()

from fastapi import Query, Depends
from psycopg2.extras import RealDictCursor


def fetch_cda_sent_data(filter: str, page: int = 1, search: str = None, conn=None):
    passed_conn = conn is not None
    if not passed_conn:
        conn = get_conn()

    limit = 50
    offset = (page - 1) * limit

    try:
        params: list = []

        summary_cte = """
            WITH brokerage_base AS (
                SELECT
                    'brokerage_engine'::text AS source_table,
                    be.transaction_identifier_transactionid AS transaction_id,
                    be.skyslopefileid::text AS skyslopefileid,
                    be.property_address,
                    be.tags,
                    be.sale_price::numeric AS be_sale_price,
                    be.closed_date::date AS be_closed_date,
                    be.contract_date::date AS be_contract_date,
                    be.listing_price::numeric AS be_listing_price,
                    be.transaction_status AS be_transaction_status,
                    be.buyer_name AS be_buyer_name,
                    be.seller_name AS be_seller_name,
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
                WHERE be.tags ILIKE '%%CdaSent%%'
            ),
            other_income_base AS (
                SELECT
                    'otherincome_transactions'::text AS source_table,
                    oit.transaction_identifier_transactionid AS transaction_id,
                    oit.skyslopefileid::text AS skyslopefileid,
                    oit.property_address,
                    oit.tags,
                    oit.income_received::numeric AS be_sale_price,
                    oit.income_received_date::date AS be_closed_date,
                    NULL::date AS be_contract_date,
                    NULL::numeric AS be_listing_price,
                    oit.transaction_status AS be_transaction_status,
                    NULL::text AS be_buyer_name,
                    NULL::text AS be_seller_name,
                    oit.gross_commission::numeric AS be_gross_commission
                FROM otherincome_transactions oit
                WHERE oit.tags ILIKE '%%CdaSent%%'
            ),
            combined_source AS (
                SELECT * FROM brokerage_base
                UNION ALL
                SELECT * FROM other_income_base
            ),
            base_summary AS (
                SELECT
                    cs.source_table,
                    cs.transaction_id,
                    cs.skyslopefileid,
                    cs.property_address,
                    cs.tags,
                    cs.be_sale_price,
                    s.saleprice::numeric AS ss_sale_price,
                    cs.be_closed_date,
                    s.escrowclosingdate::date AS ss_closed_date,
                    cs.be_contract_date,
                    s.contractacceptancedate::date AS ss_contract_date,
                    cs.be_listing_price,
                    s.listingprice::numeric AS ss_listing_price,
                    cs.be_transaction_status,
                    s.status AS ss_transaction_status,
                    cs.be_gross_commission,
                    CASE
                        WHEN cs.tags ILIKE '%%listingside%%' AND cs.tags ILIKE '%%sellingside%%'
                            THEN scn.officegrosscommissiononsale
                        WHEN cs.tags ILIKE '%%listingside%%'
                            THEN COALESCE(scn.listingcommissionamount, scn.officegrosscommissiononsale)
                        WHEN cs.tags ILIKE '%%sellingside%%'
                            THEN COALESCE(scn.salecommissionamount, scn.officegrosscommissiononsale)
                        ELSE COALESCE(scn.salecommissionamount, scn.officegrosscommissiononsale)
                    END AS ss_gross_commission,
                    CASE
                        WHEN cs.skyslopefileid IS NULL OR s.saleguid IS NULL THEN TRUE
                        WHEN cs.be_sale_price IS DISTINCT FROM s.saleprice::numeric THEN TRUE
                        ELSE FALSE
                    END AS sale_price_mismatch,
                    CASE
                        WHEN cs.skyslopefileid IS NULL OR s.saleguid IS NULL THEN TRUE
                        WHEN cs.be_closed_date IS DISTINCT FROM s.escrowclosingdate::date THEN TRUE
                        ELSE FALSE
                    END AS closed_date_mismatch,
                    CASE
                        WHEN cs.source_table = 'otherincome_transactions' THEN NULL
                        WHEN cs.skyslopefileid IS NULL OR s.saleguid IS NULL THEN TRUE
                        WHEN cs.be_contract_date IS DISTINCT FROM s.contractacceptancedate::date THEN TRUE
                        ELSE FALSE
                    END AS contract_date_mismatch,
                    CASE
                        WHEN cs.source_table = 'otherincome_transactions' THEN NULL
                        WHEN cs.skyslopefileid IS NULL OR s.saleguid IS NULL THEN TRUE
                        WHEN cs.be_listing_price IS DISTINCT FROM s.listingprice::numeric THEN TRUE
                        ELSE FALSE
                    END AS listing_price_mismatch_db,
                    CASE
                        WHEN cs.skyslopefileid IS NULL OR s.saleguid IS NULL THEN TRUE
                        WHEN LOWER(s.status) = 'expired' THEN NULL
                        WHEN cs.be_transaction_status IS NULL OR s.status IS NULL THEN NULL
                        WHEN LOWER(cs.be_transaction_status) = LOWER(s.status) THEN FALSE
                        WHEN LOWER(cs.be_transaction_status) = 'cancelled'
                             AND LOWER(s.status) IN ('canceled/app', 'canceled/pend')
                        THEN FALSE
                        WHEN LOWER(cs.be_transaction_status) = 'closed'
                             AND LOWER(s.status) IN ('archived', 'closed')
                        THEN FALSE
                        ELSE TRUE
                    END AS transaction_status_mismatch,
                    CASE
                        WHEN cs.skyslopefileid IS NULL OR s.saleguid IS NULL THEN 'mismatch'
                        WHEN cs.tags ILIKE '%%listingside%%' AND cs.tags ILIKE '%%sellingside%%'
                            THEN CASE
                                WHEN scn.officegrosscommissiononsale IS NULL
                                  OR cs.be_gross_commission IS NULL
                                  OR scn.officegrosscommissiononsale = 0
                                  OR cs.be_gross_commission = 0
                                THEN NULL
                                WHEN ROUND(scn.officegrosscommissiononsale::numeric, 2)
                                     IS DISTINCT FROM ROUND(cs.be_gross_commission::numeric, 2)
                                THEN 'mismatch'
                                ELSE 'match'
                            END
                        WHEN cs.tags ILIKE '%%listingside%%'
                            THEN CASE
                                WHEN COALESCE(scn.listingcommissionamount, scn.officegrosscommissiononsale) IS NULL
                                  OR cs.be_gross_commission IS NULL
                                  OR COALESCE(scn.listingcommissionamount, scn.officegrosscommissiononsale) = 0
                                  OR cs.be_gross_commission = 0
                                THEN NULL
                                WHEN ROUND(COALESCE(scn.listingcommissionamount, scn.officegrosscommissiononsale)::numeric, 2)
                                     IS DISTINCT FROM ROUND(cs.be_gross_commission::numeric, 2)
                                THEN 'mismatch'
                                ELSE 'match'
                            END
                        ELSE
                            CASE
                                WHEN COALESCE(scn.salecommissionamount, scn.officegrosscommissiononsale) IS NULL
                                  OR cs.be_gross_commission IS NULL
                                  OR COALESCE(scn.salecommissionamount, scn.officegrosscommissiononsale) = 0
                                  OR cs.be_gross_commission = 0
                                THEN NULL
                                WHEN ROUND(COALESCE(scn.salecommissionamount, scn.officegrosscommissiononsale)::numeric, 2)
                                     IS DISTINCT FROM ROUND(cs.be_gross_commission::numeric, 2)
                                THEN 'mismatch'
                                ELSE 'match'
                            END
                    END AS gross_commission_mismatch,
                    CASE
                        WHEN cs.skyslopefileid IS NULL OR s.saleguid IS NULL THEN TRUE
                        ELSE FALSE
                    END AS no_skyslope_record
                FROM combined_source cs
                LEFT JOIN sale s
                    ON s.saleguid = cs.skyslopefileid::uuid
                LEFT JOIN sale_commission scn
                    ON scn.saleguid = s.saleguid
            )
        """

        summary_query = f"""
            {summary_cte}
            SELECT
                COUNT(*) AS total_cda_sent,
                COUNT(*) FILTER (
                    WHERE
                        no_skyslope_record = TRUE
                        OR sale_price_mismatch = TRUE
                        OR closed_date_mismatch = TRUE
                        OR contract_date_mismatch = TRUE
                        OR transaction_status_mismatch = TRUE
                        OR gross_commission_mismatch = 'mismatch'
                ) AS unmatched_count,
                COUNT(*) FILTER (
                    WHERE
                        no_skyslope_record = FALSE
                        AND COALESCE(sale_price_mismatch, FALSE) = FALSE
                        AND COALESCE(closed_date_mismatch, FALSE) = FALSE
                        AND COALESCE(contract_date_mismatch, FALSE) = FALSE
                        AND COALESCE(transaction_status_mismatch, FALSE) = FALSE
                        AND COALESCE(gross_commission_mismatch, 'match') <> 'mismatch'
                ) AS matched_count,
                COUNT(*) FILTER (WHERE no_skyslope_record = TRUE) AS no_skyslope_record
            FROM base_summary;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(summary_query)
            summary = cur.fetchone()

        if filter == "no_skyslope":
            search_conditions = []
            no_skyslope_params = []

            if search:
                search_conditions.append("""
                    (
                        CAST(x.transaction_id AS TEXT) ILIKE %s
                        OR x.property_address ILIKE %s
                    )
                """)
                search_term = f"%{search}%"
                no_skyslope_params.extend([search_term, search_term])

            where_clause = ""
            if search_conditions:
                where_clause = "WHERE " + " AND ".join(search_conditions)

            no_skyslope_base = """
                WITH no_skyslope_base AS (
                    SELECT
                        'brokerage_engine'::text AS source_table,
                        be.transaction_identifier_transactionid AS transaction_id,
                        be.skyslopefileid::text AS skyslopefileid,
                        be.property_address,
                        be.tags,
                        be.sale_price::numeric AS be_sale_price,
                        NULL::numeric AS ss_sale_price,
                        be.closed_date::date AS be_closed_date,
                        NULL::date AS ss_closed_date,
                        be.contract_date::date AS be_contract_date,
                        NULL::date AS ss_contract_date,
                        be.listing_price::numeric AS be_listing_price,
                        NULL::numeric AS ss_listing_price,
                        be.transaction_status AS be_transaction_status,
                        NULL::text AS ss_transaction_status,
                        be.buyer_name AS be_buyer_name,
                        NULL::text AS ss_buyer_name,
                        be.seller_name AS be_seller_name,
                        NULL::text AS ss_seller_name,
                        CASE
                            WHEN be.tags ILIKE '%%listingside%%' AND be.tags ILIKE '%%sellingside%%'
                                THEN be.total_gross_commission
                            WHEN be.tags ILIKE '%%listingside%%'
                                THEN be.listing_side_gross_commission
                            WHEN be.tags ILIKE '%%sellingside%%'
                                THEN be.buying_side_gross_commission
                            ELSE be.buying_side_gross_commission
                        END AS be_gross_commission,
                        NULL::numeric AS ss_gross_commission,
                        TRUE AS no_skyslope_record
                    FROM brokerage_engine be
                    LEFT JOIN sale s
                        ON s.saleguid = be.skyslopefileid::uuid
                    WHERE be.tags ILIKE '%%CdaSent%%'
                      AND (be.skyslopefileid IS NULL OR s.saleguid IS NULL)

                    UNION ALL

                    SELECT
                        'otherincome_transactions'::text AS source_table,
                        oit.transaction_identifier_transactionid AS transaction_id,
                        oit.skyslopefileid::text AS skyslopefileid,
                        oit.property_address,
                        oit.tags,
                        oit.income_received::numeric AS be_sale_price,
                        NULL::numeric AS ss_sale_price,
                        oit.income_received_date::date AS be_closed_date,
                        NULL::date AS ss_closed_date,
                        NULL::date AS be_contract_date,
                        NULL::date AS ss_contract_date,
                        NULL::numeric AS be_listing_price,
                        NULL::numeric AS ss_listing_price,
                        oit.transaction_status AS be_transaction_status,
                        NULL::text AS ss_transaction_status,
                        NULL::text AS be_buyer_name,
                        NULL::text AS ss_buyer_name,
                        NULL::text AS be_seller_name,
                        NULL::text AS ss_seller_name,
                        oit.gross_commission::numeric AS be_gross_commission,
                        NULL::numeric AS ss_gross_commission,
                        TRUE AS no_skyslope_record
                    FROM otherincome_transactions oit
                    LEFT JOIN sale s
                        ON s.saleguid = oit.skyslopefileid::uuid
                    WHERE oit.tags ILIKE '%%CdaSent%%'
                      AND (oit.skyslopefileid IS NULL OR s.saleguid IS NULL)
                )
            """

            count_query = f"""
                {no_skyslope_base}
                SELECT COUNT(*) AS count
                FROM no_skyslope_base x
                {where_clause};
            """

            data_query = f"""
                {no_skyslope_base}
                SELECT
                    x.source_table,
                    x.transaction_id,
                    x.skyslopefileid,
                    x.property_address,
                    x.tags,
                    TRUE AS is_stale,
                    x.be_gross_commission,
                    x.ss_gross_commission,
                    'mismatch'::text AS gross_commission_mismatch,
                    x.be_closed_date,
                    x.ss_closed_date,
                    TRUE AS closed_date_mismatch,
                    x.be_sale_price,
                    x.ss_sale_price,
                    TRUE AS sale_price_mismatch,
                    x.be_transaction_status,
                    x.ss_transaction_status,
                    TRUE AS transaction_status_mismatch,
                    x.be_contract_date,
                    x.ss_contract_date,
                    TRUE AS contract_date_mismatch,
                    x.be_listing_price,
                    x.ss_listing_price,
                    NULL::text AS listing_price_mismatch,
                    x.be_buyer_name,
                    x.ss_buyer_name,
                    NULL::text AS buyer_name_comparison,
                    x.be_seller_name,
                    x.ss_seller_name,
                    NULL::text AS seller_name_comparison,
                    x.no_skyslope_record
                FROM no_skyslope_base x
                {where_clause}
                ORDER BY x.transaction_id
                LIMIT %s OFFSET %s;
            """

            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(count_query, no_skyslope_params)
                total_count = cur.fetchone()["count"]

                cur.execute(data_query, no_skyslope_params + [limit, offset])
                rows = cur.fetchall()

            comparison_total = (summary["matched_count"] or 0) + (summary["unmatched_count"] or 0)
            match_percentage = round(((summary["matched_count"] or 0) / comparison_total) * 100, 2) if comparison_total else 0
            total_pages = (total_count + limit - 1) // limit

            return {
                "filter": filter,
                "summary": {
                    "count": total_count,
                    "total_cda_sent": summary["total_cda_sent"],
                    "unmatched_count": summary["unmatched_count"],
                    "match_percentage": match_percentage,
                    "no_skyslope_record": summary["no_skyslope_record"],
                },
                "page": page,
                "page_size": limit,
                "total_pages": total_pages,
                "data": [dict(row) for row in rows],
            }

        search_clause = ""
        if search:
            search_clause = """
                WHERE
                    CAST(b.transaction_id AS TEXT) ILIKE %s
                    OR b.property_address ILIKE %s
            """
            search_term = f"%{search}%"
            params.extend([search_term, search_term])

        base_cte = """
            WITH brokerage_base AS (
                SELECT
                    'brokerage_engine'::text AS source_table,
                    be.transaction_identifier_transactionid AS transaction_id,
                    be.skyslopefileid::text AS skyslopefileid,
                    be.property_address,
                    be.tags,
                    be.sale_price::numeric AS be_sale_price,
                    be.closed_date::date AS be_closed_date,
                    be.contract_date::date AS be_contract_date,
                    be.listing_price::numeric AS be_listing_price,
                    be.transaction_status AS be_transaction_status,
                    be.buyer_name AS be_buyer_name,
                    be.seller_name AS be_seller_name,
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
                WHERE be.tags ILIKE '%%CdaSent%%'
            ),
            other_income_base AS (
                SELECT
                    'otherincome_transactions'::text AS source_table,
                    oit.transaction_identifier_transactionid AS transaction_id,
                    oit.skyslopefileid::text AS skyslopefileid,
                    oit.property_address,
                    oit.tags,
                    oit.income_received::numeric AS be_sale_price,
                    oit.income_received_date::date AS be_closed_date,
                    NULL::date AS be_contract_date,
                    NULL::numeric AS be_listing_price,
                    oit.transaction_status AS be_transaction_status,
                    NULL::text AS be_buyer_name,
                    NULL::text AS be_seller_name,
                    oit.gross_commission::numeric AS be_gross_commission
                FROM otherincome_transactions oit
                WHERE oit.tags ILIKE '%%CdaSent%%'
            ),
            combined_source AS (
                SELECT * FROM brokerage_base
                UNION ALL
                SELECT * FROM other_income_base
            ),
            buyer_contacts AS (
                SELECT
                    sc.saleguid,
                    STRING_AGG(
                        TRIM(COALESCE(sc.firstname, '') || ' ' || COALESCE(sc.lastname, '')),
                        ', '
                    ) AS ss_buyer_name
                FROM sale_contact sc
                WHERE LOWER(sc.role) = 'buyer'
                GROUP BY sc.saleguid
            ),
            seller_contacts AS (
                SELECT
                    sc.saleguid,
                    STRING_AGG(
                        TRIM(COALESCE(sc.firstname, '') || ' ' || COALESCE(sc.lastname, '')),
                        ', '
                    ) AS ss_seller_name
                FROM sale_contact sc
                WHERE LOWER(sc.role) = 'seller'
                GROUP BY sc.saleguid
            ),
            base AS (
                SELECT
                    cs.source_table,
                    cs.transaction_id,
                    cs.skyslopefileid,
                    cs.property_address,
                    cs.tags,
                    cs.be_sale_price,
                    s.saleprice::numeric AS ss_sale_price,
                    cs.be_closed_date,
                    s.escrowclosingdate::date AS ss_closed_date,
                    cs.be_contract_date,
                    s.contractacceptancedate::date AS ss_contract_date,
                    cs.be_listing_price,
                    s.listingprice::numeric AS ss_listing_price,
                    cs.be_transaction_status,
                    s.status AS ss_transaction_status,
                    cs.be_buyer_name,
                    cs.be_seller_name,
                    COALESCE(bc.ss_buyer_name, '') AS ss_buyer_name,
                    COALESCE(sc.ss_seller_name, '') AS ss_seller_name,
                    cs.be_gross_commission,
                    CASE
                        WHEN cs.tags ILIKE '%%listingside%%' AND cs.tags ILIKE '%%sellingside%%'
                            THEN scn.officegrosscommissiononsale
                        WHEN cs.tags ILIKE '%%listingside%%'
                            THEN COALESCE(scn.listingcommissionamount, scn.officegrosscommissiononsale)
                        WHEN cs.tags ILIKE '%%sellingside%%'
                            THEN COALESCE(scn.salecommissionamount, scn.officegrosscommissiononsale)
                        ELSE COALESCE(scn.salecommissionamount, scn.officegrosscommissiononsale)
                    END AS ss_gross_commission,
                    CASE
                        WHEN cs.skyslopefileid IS NULL OR s.saleguid IS NULL THEN TRUE
                        WHEN cs.be_sale_price IS DISTINCT FROM s.saleprice::numeric THEN TRUE
                        ELSE FALSE
                    END AS sale_price_mismatch,
                    CASE
                        WHEN cs.skyslopefileid IS NULL OR s.saleguid IS NULL THEN TRUE
                        WHEN cs.be_closed_date IS DISTINCT FROM s.escrowclosingdate::date THEN TRUE
                        ELSE FALSE
                    END AS closed_date_mismatch,
                    CASE
                        WHEN cs.source_table = 'otherincome_transactions' THEN NULL
                        WHEN cs.skyslopefileid IS NULL OR s.saleguid IS NULL THEN TRUE
                        WHEN cs.be_contract_date IS DISTINCT FROM s.contractacceptancedate::date THEN TRUE
                        ELSE FALSE
                    END AS contract_date_mismatch,
                    CASE
                        WHEN cs.source_table = 'otherincome_transactions' THEN NULL
                        WHEN cs.skyslopefileid IS NULL OR s.saleguid IS NULL THEN TRUE
                        WHEN cs.be_listing_price IS DISTINCT FROM s.listingprice::numeric THEN TRUE
                        ELSE FALSE
                    END AS listing_price_mismatch_db,
                    CASE
                        WHEN cs.skyslopefileid IS NULL OR s.saleguid IS NULL THEN TRUE
                        WHEN LOWER(s.status) = 'expired' THEN NULL
                        WHEN cs.be_transaction_status IS NULL OR s.status IS NULL THEN NULL
                        WHEN LOWER(cs.be_transaction_status) = LOWER(s.status) THEN FALSE
                        WHEN LOWER(cs.be_transaction_status) = 'cancelled'
                             AND LOWER(s.status) IN ('canceled/app', 'canceled/pend')
                        THEN FALSE
                        WHEN LOWER(cs.be_transaction_status) = 'closed'
                             AND LOWER(s.status) IN ('archived', 'closed')
                        THEN FALSE
                        ELSE TRUE
                    END AS transaction_status_mismatch,
                    CASE
                        WHEN cs.skyslopefileid IS NULL OR s.saleguid IS NULL THEN 'mismatch'
                        WHEN cs.tags ILIKE '%%listingside%%' AND cs.tags ILIKE '%%sellingside%%'
                            THEN CASE
                                WHEN scn.officegrosscommissiononsale IS NULL
                                  OR cs.be_gross_commission IS NULL
                                  OR scn.officegrosscommissiononsale = 0
                                  OR cs.be_gross_commission = 0
                                THEN NULL
                                WHEN ROUND(scn.officegrosscommissiononsale::numeric, 2)
                                     IS DISTINCT FROM ROUND(cs.be_gross_commission::numeric, 2)
                                THEN 'mismatch'
                                ELSE 'match'
                            END
                        WHEN cs.tags ILIKE '%%listingside%%'
                            THEN CASE
                                WHEN COALESCE(scn.listingcommissionamount, scn.officegrosscommissiononsale) IS NULL
                                  OR cs.be_gross_commission IS NULL
                                  OR COALESCE(scn.listingcommissionamount, scn.officegrosscommissiononsale) = 0
                                  OR cs.be_gross_commission = 0
                                THEN NULL
                                WHEN ROUND(COALESCE(scn.listingcommissionamount, scn.officegrosscommissiononsale)::numeric, 2)
                                     IS DISTINCT FROM ROUND(cs.be_gross_commission::numeric, 2)
                                THEN 'mismatch'
                                ELSE 'match'
                            END
                        ELSE
                            CASE
                                WHEN COALESCE(scn.salecommissionamount, scn.officegrosscommissiononsale) IS NULL
                                  OR cs.be_gross_commission IS NULL
                                  OR COALESCE(scn.salecommissionamount, scn.officegrosscommissiononsale) = 0
                                  OR cs.be_gross_commission = 0
                                THEN NULL
                                WHEN ROUND(COALESCE(scn.salecommissionamount, scn.officegrosscommissiononsale)::numeric, 2)
                                     IS DISTINCT FROM ROUND(cs.be_gross_commission::numeric, 2)
                                THEN 'mismatch'
                                ELSE 'match'
                            END
                    END AS gross_commission_mismatch,
                    CASE
                        WHEN cs.skyslopefileid IS NULL OR s.saleguid IS NULL THEN TRUE
                        ELSE FALSE
                    END AS no_skyslope_record
                FROM combined_source cs
                LEFT JOIN sale s
                    ON s.saleguid = cs.skyslopefileid::uuid
                LEFT JOIN sale_commission scn
                    ON scn.saleguid = s.saleguid
                LEFT JOIN buyer_contacts bc
                    ON bc.saleguid = s.saleguid
                LEFT JOIN seller_contacts sc
                    ON sc.saleguid = s.saleguid
            )
        """

        filter_clause = ""
        if filter == "mismatch":
            filter_clause = """
                WHERE
                    b.no_skyslope_record = TRUE
                    OR b.sale_price_mismatch = TRUE
                    OR b.closed_date_mismatch = TRUE
                    OR b.contract_date_mismatch = TRUE
                    OR b.transaction_status_mismatch = TRUE
                    OR b.gross_commission_mismatch = 'mismatch'
            """

        where_parts = []
        if filter_clause:
            where_parts.append(filter_clause.replace("WHERE", "").strip())
        if search_clause:
            where_parts.append(search_clause.replace("WHERE", "").strip())

        final_where = ""
        if where_parts:
            final_where = "WHERE " + " AND ".join(f"({p})" for p in where_parts)

        count_query = f"""
            {base_cte}
            SELECT COUNT(*) AS count
            FROM base b
            {final_where};
        """

        data_query = f"""
            {base_cte}
            SELECT
                b.source_table,
                b.transaction_id,
                b.skyslopefileid,
                b.property_address,
                b.tags,
                b.be_gross_commission,
                b.ss_gross_commission,
                b.gross_commission_mismatch,
                b.be_closed_date,
                b.ss_closed_date,
                b.closed_date_mismatch,
                b.be_sale_price,
                b.ss_sale_price,
                b.sale_price_mismatch,
                b.be_transaction_status,
                b.ss_transaction_status,
                b.transaction_status_mismatch,
                b.be_contract_date,
                b.ss_contract_date,
                b.contract_date_mismatch,
                b.be_listing_price,
                b.ss_listing_price,
                b.listing_price_mismatch_db,
                b.be_buyer_name,
                b.ss_buyer_name,
                b.be_seller_name,
                b.ss_seller_name,
                b.no_skyslope_record
            FROM base b
            {final_where}
            ORDER BY b.transaction_id
            LIMIT %s OFFSET %s;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(count_query, params)
            total_count = cur.fetchone()["count"]

            cur.execute(data_query, params + [limit, offset])
            rows = cur.fetchall()

        reshaped_rows = []
        for row in rows:
            is_brokerage = row["source_table"] == "brokerage_engine"

            buyer_result = (
                compare_names(row["ss_buyer_name"], row["be_buyer_name"])
                if is_brokerage else None
            )
            seller_result = (
                compare_names(row["ss_seller_name"], row["be_seller_name"])
                if is_brokerage else None
            )
            listing_price_result = (
                compare_listing_price(row["be_listing_price"], row["ss_listing_price"])
                if is_brokerage else None
            )

            is_stale = (
                row["no_skyslope_record"] is True
                or row["sale_price_mismatch"] is True
                or row["closed_date_mismatch"] is True
                or row["contract_date_mismatch"] is True
                or listing_price_result == "mismatch"
                or row["transaction_status_mismatch"] is True
                or buyer_result == "mismatch"
                or seller_result == "mismatch"
                or row["gross_commission_mismatch"] == "mismatch"
            )

            reshaped_row = {
                "source_table": row["source_table"],
                "transaction_id": row["transaction_id"],
                "skyslopefileid": row["skyslopefileid"],
                "property_address": row["property_address"],
                "tags": row["tags"],
                "is_stale": is_stale,
                "be_gross_commission": row["be_gross_commission"],
                "ss_gross_commission": row["ss_gross_commission"],
                "gross_commission_mismatch": row["gross_commission_mismatch"],
                "be_closed_date": row["be_closed_date"],
                "ss_closed_date": row["ss_closed_date"],
                "closed_date_mismatch": row["closed_date_mismatch"],
                "be_sale_price": row["be_sale_price"],
                "ss_sale_price": row["ss_sale_price"],
                "sale_price_mismatch": row["sale_price_mismatch"],
                "be_transaction_status": row["be_transaction_status"],
                "ss_transaction_status": row["ss_transaction_status"],
                "transaction_status_mismatch": row["transaction_status_mismatch"],
                "be_contract_date": row["be_contract_date"],
                "ss_contract_date": row["ss_contract_date"],
                "contract_date_mismatch": row["contract_date_mismatch"],
                "be_listing_price": row["be_listing_price"],
                "ss_listing_price": row["ss_listing_price"],
                "listing_price_mismatch": listing_price_result,
                "be_buyer_name": row["be_buyer_name"],
                "ss_buyer_name": row["ss_buyer_name"],
                "buyer_name_comparison": buyer_result,
                "be_seller_name": row["be_seller_name"],
                "ss_seller_name": row["ss_seller_name"],
                "seller_name_comparison": seller_result,
            }

            if filter == "all":
                reshaped_rows.append(reshaped_row)
            elif filter == "mismatch" and is_stale:
                reshaped_rows.append(reshaped_row)

        comparison_total = (summary["matched_count"] or 0) + (summary["unmatched_count"] or 0)
        match_percentage = round(((summary["matched_count"] or 0) / comparison_total) * 100, 2) if comparison_total else 0

        total_pages = (total_count + limit - 1) // limit

        return {
            "filter": filter,
            "summary": {
                "count": total_count,
                "total_cda_sent": summary["total_cda_sent"],
                "unmatched_count": summary["unmatched_count"],
                "match_percentage": match_percentage,
                "no_skyslope_record": summary["no_skyslope_record"],
            },
            "page": page,
            "page_size": limit,
            "total_pages": total_pages,
            "data": reshaped_rows,
        }

    finally:
        if not passed_conn:
            conn.close()


@router.get("/cda-sent/listing")
def get_cda_sent(
    filter: str = Query("all", enum=["all", "mismatch", "no_skyslope"]),
    page: int = Query(default=1, ge=1),
    search: str = Query(default=None),
    conn=Depends(get_db)
):
    return fetch_cda_sent_data(filter, page, search, conn)


@router.get("/cda-sent/download")
def download_cda_sent(
    filter: str = Query("all", enum=["all", "mismatch", "no_skyslope"]),
    conn=Depends(get_db)
):
    result = fetch_cda_sent_data(filter, conn)
    data = result["data"]

    if filter == "no_skyslope":
        columns_map = {
            "transaction_id": "Transaction ID",
            "skyslopefileid": "SkySlope File ID",
            "property_address": "Property Address",
            "tags": "Tags",
            "be_sale_price": "BE Sale Price",
            "be_closed_date": "BE Closed Date",
            "be_contract_date": "BE Contract Date",
            "be_listing_price": "BE Listing Price",
            "be_transaction_status": "BE Transaction Status",
            "be_buyer_name": "BE Buyer Name",
            "be_seller_name": "BE Seller Name"
        }
    else:
        columns_map = {
            "transaction_id": "Transaction ID",
            "skyslopefileid": "SkySlope File ID",
            "property_address": "Property Address",
            "tags": "Tags",
            "is_stale": "Is Stale",
            "be_gross_commission": "BE Gross Commission",
            "ss_gross_commission": "SS Gross Commission",
            "gross_commission_mismatch": "Gross Commission Mismatch",
            "be_closed_date": "BE Closed Date",
            "ss_closed_date": "SS Closed Date",
            "closed_date_mismatch": "Closed Date Mismatch",
            "be_sale_price": "BE Sale Price",
            "ss_sale_price": "SS Sale Price",
            "sale_price_mismatch": "Sale Price Mismatch",
            "be_transaction_status": "BE Transaction Status",
            "ss_transaction_status": "SS Transaction Status",
            "transaction_status_mismatch": "Transaction Status Mismatch",
            "be_contract_date": "BE Contract Date",
            "ss_contract_date": "SS Contract Date",
            "contract_date_mismatch": "Contract Date Mismatch",
            "be_listing_price": "BE Listing Price",
            "ss_listing_price": "SS Listing Price",
            "listing_price_mismatch": "Listing Price Mismatch",
            "be_buyer_name": "BE Buyer Name",
            "ss_buyer_name": "SS Buyer Name",
            "buyer_name_comparison": "Buyer Name Comparison",
            "be_seller_name": "BE Seller Name",
            "ss_seller_name": "SS Seller Name",
            "seller_name_comparison": "Seller Name Comparison"
        }

    rows_to_export = []
    for r in data:
        row_dict = {}
        for key, header in columns_map.items():
            val = r.get(key)
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
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name="CDA Sent Report", index=False)
        
        workbook = writer.book
        worksheet = writer.sheets["CDA Sent Report"]
        
        # Explicitly show grid lines
        worksheet.views.sheetView[0].showGridLines = True
        
        # Premium styling definitions
        font_header = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
        fill_header = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
        align_header = Alignment(horizontal="center", vertical="center", wrap_text=True)
        
        font_body = Font(name="Segoe UI", size=10)
        fill_even = PatternFill(start_color="F9FAFB", end_color="F9FAFB", fill_type="solid") # subtle gray zebra striping
        fill_mismatch = PatternFill(start_color="FCE8E6", end_color="FCE8E6", fill_type="solid") # soft pastel red
        font_mismatch = Font(name="Segoe UI", size=10, bold=True, color="C53929") # clear dark red warning text
        
        thin_border = Border(
            left=Side(style='thin', color='D0D5DD'),
            right=Side(style='thin', color='D0D5DD'),
            top=Side(style='thin', color='D0D5DD'),
            bottom=Side(style='thin', color='D0D5DD')
        )
        
        # Apply header styling
        worksheet.row_dimensions[1].height = 28
        for col_num in range(1, len(df.columns) + 1):
            cell = worksheet.cell(row=1, column=col_num)
            cell.font = font_header
            cell.fill = fill_header
            cell.alignment = align_header
            cell.border = thin_border
            
        # Classify columns for appropriate aligning/formatting
        currency_cols = []
        date_cols = []
        center_cols = []
        
        currency_keywords = ["gross commission", "sale price", "listing price"]
        date_keywords = ["closed date", "contract date"]
        center_keywords = ["id", "is stale", "mismatch", "comparison", "tags"]
        
        for idx, col_name in enumerate(df.columns):
            col_name_lower = col_name.lower()
            if any(kw in col_name_lower for kw in currency_keywords):
                currency_cols.append(idx + 1)
            elif any(kw in col_name_lower for kw in date_keywords):
                date_cols.append(idx + 1)
            elif any(kw in col_name_lower for kw in center_keywords):
                center_cols.append(idx + 1)
                
        # Style body rows
        for row_num in range(2, len(df) + 2):
            worksheet.row_dimensions[row_num].height = 20
            is_even_row = (row_num % 2 == 0)
            
            for col_num in range(1, len(df.columns) + 1):
                cell = worksheet.cell(row=row_num, column=col_num)
                cell.font = font_body
                cell.border = thin_border
                
                # Apply Zebra striping as base fill
                if is_even_row:
                    cell.fill = fill_even
                
                val = cell.value
                val_str = str(val).strip().lower() if val is not None else ""
                col_name = df.columns[col_num - 1]
                col_name_lower = col_name.lower()
                
                # Highlight mismatches
                is_cell_mismatch = False
                if "mismatch" in col_name_lower or "comparison" in col_name_lower:
                    if val_str in ["yes", "mismatch"]:
                        is_cell_mismatch = True
                elif col_name_lower == "is stale":
                    if val_str == "yes":
                        is_cell_mismatch = True
                        
                if is_cell_mismatch:
                    cell.fill = fill_mismatch
                    cell.font = font_mismatch
                
                # Apply alignment and number formats
                if col_num in currency_cols:
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                    if isinstance(val, (int, float)):
                        cell.number_format = '$#,##0.00'
                elif col_num in date_cols:
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                elif col_num in center_cols:
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="center")
                    
        # Adjust column widths dynamically to prevent clipping
        for col in worksheet.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val = cell.value
                if val is not None:
                    if cell.column in currency_cols and isinstance(val, (int, float)):
                        val_len = len(f"${val:,.2f}")
                    else:
                        val_len = len(str(val))
                    if val_len > max_len:
                        max_len = val_len
            worksheet.column_dimensions[col_letter].width = max(max_len + 3, 12)

    output.seek(0)
    
    filename = f"cda_sent_report_{filter}.xlsx"
    headers = {
        'Content-Disposition': f'attachment; filename="{filename}"'
    }
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers
    )
