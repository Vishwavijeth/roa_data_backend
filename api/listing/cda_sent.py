from fastapi import APIRouter
from db import get_conn
from psycopg2.extras import RealDictCursor
from services.comparison import compare_names, compare_listing_price
from fastapi import Query

router = APIRouter()

@router.get("/cda-sent/listing")
def get_cda_sent(filter: str = Query("all", enum=["all", "mismatch", "no_skyslope"])):
    conn = get_conn()
    try:
        # For no_skyslope filter, run a simpler query and return early
        if filter == "no_skyslope":
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        be.transaction_identifier_transactionid AS transaction_id,
                        be.skyslopefileid,
                        be.property_address,
                        be.tags,
                        be.sale_price AS be_sale_price,
                        be.closed_date AS be_closed_date,
                        be.contract_date AS be_contract_date,
                        be.listing_price AS be_listing_price,
                        be.transaction_status AS be_transaction_status,
                        be.buyer_name AS be_buyer_name,
                        be.seller_name AS be_seller_name
                    FROM brokerage_engine be
                    WHERE be.tags LIKE '%CdaSent%'
                    AND be.skyslopefileid IS NULL
                    ORDER BY be.transaction_identifier_transactionid;
                """)
                no_skyslope_rows = cur.fetchall()
                cur.execute("""
                    SELECT COUNT(*) AS total_cda_sent
                    FROM brokerage_engine be
                    WHERE be.tags LIKE '%CdaSent%'
                """)
                summary = cur.fetchone()
                cur.execute("""
                    SELECT COUNT(*) AS no_skyslope_record
                    FROM brokerage_engine be
                    WHERE be.tags LIKE '%CdaSent%'
                    AND be.skyslopefileid IS NULL
                """)
                no_skyslope = cur.fetchone()
            return {
                "filter": filter,
                "total_cda_sent": summary["total_cda_sent"],
                "unmatched_count": None,
                "no_skyslope_record": no_skyslope["no_skyslope_record"],
                "data": [dict(row) for row in no_skyslope_rows],
            }
        # all / mismatch filters
        query = """
            WITH base AS (
                SELECT
                    be.transaction_identifier_transactionid AS transaction_id,
                    be.skyslopefileid,
                    be.property_address,
                    be.tags,
                    be.sale_price AS be_sale_price,
                    s.saleprice AS ss_sale_price,
                    be.closed_date AS be_closed_date,
                    s.escrowclosingdate AS ss_closed_date,
                    be.contract_date AS be_contract_date,
                    s.contractacceptancedate AS ss_contract_date,
                    be.listing_price AS be_listing_price,
                    s.listingprice AS ss_listing_price,
                    be.transaction_status AS be_transaction_status,
                    s.status AS ss_transaction_status,
                    CASE
                        WHEN be.tags ILIKE '%%listingside%%' AND be.tags ILIKE '%%sellingside%%'
                            THEN be.total_gross_commission
                        WHEN be.tags ILIKE '%%listingside%%'
                            THEN be.listing_side_gross_commission
                        WHEN be.tags ILIKE '%%sellingside%%'
                            THEN be.buying_side_gross_commission
                        ELSE be.buying_side_gross_commission
                    END AS be_gross_commission,
                    scn.officegrosscommissiononsale AS ss_gross_commission,
                    COALESCE(
                        (
                            SELECT STRING_AGG(
                                TRIM(COALESCE(sc.firstname, '') || ' ' || COALESCE(sc.lastname, '')),
                                ', '
                            )
                            FROM sale_contact sc
                            WHERE sc.saleguid = s.saleguid
                            AND LOWER(sc.role) = 'buyer'
                        ),
                        ''
                    ) AS ss_buyer_name,
                    be.buyer_name AS be_buyer_name,
                    COALESCE(
                        (
                            SELECT STRING_AGG(
                                TRIM(COALESCE(sc.firstname, '') || ' ' || COALESCE(sc.lastname, '')),
                                ', '
                            )
                            FROM sale_contact sc
                            WHERE sc.saleguid = s.saleguid
                            AND LOWER(sc.role) = 'seller'
                        ),
                        ''
                    ) AS ss_seller_name,
                    be.seller_name AS be_seller_name,
                    CASE
                        WHEN be.sale_price IS DISTINCT FROM s.saleprice
                        THEN true
                        ELSE false
                    END AS sale_price_mismatch,
                    CASE
                        WHEN be.closed_date IS DISTINCT FROM s.escrowclosingdate
                        THEN true
                        ELSE false
                    END AS closed_date_mismatch,
                    CASE
                        WHEN be.contract_date IS DISTINCT FROM s.contractacceptancedate
                        THEN true
                        ELSE false
                    END AS contract_date_mismatch,
                    CASE
                        WHEN LOWER(s.status) = 'expired' THEN NULL
                        WHEN be.transaction_status IS NULL OR s.status IS NULL THEN NULL
                        WHEN LOWER(be.transaction_status) = LOWER(s.status) THEN false
                        WHEN LOWER(be.transaction_status) = 'cancelled'
                             AND LOWER(s.status) IN ('canceled/app', 'canceled/pend')
                        THEN false
                        ELSE true
                    END AS transaction_status_mismatch,
                    CASE
                        WHEN scn.officegrosscommissiononsale IS NULL
                            OR (
                                CASE
                                    WHEN be.tags ILIKE '%%listingside%%' AND be.tags ILIKE '%%sellingside%%'
                                        THEN be.total_gross_commission
                                    WHEN be.tags ILIKE '%%listingside%%'
                                        THEN be.listing_side_gross_commission
                                    WHEN be.tags ILIKE '%%sellingside%%'
                                        THEN be.buying_side_gross_commission
                                    ELSE be.buying_side_gross_commission
                                END
                            ) IS NULL
                            OR scn.officegrosscommissiononsale = 0
                            OR (
                                CASE
                                    WHEN be.tags ILIKE '%%listingside%%' AND be.tags ILIKE '%%sellingside%%'
                                        THEN be.total_gross_commission
                                    WHEN be.tags ILIKE '%%listingside%%'
                                        THEN be.listing_side_gross_commission
                                    WHEN be.tags ILIKE '%%sellingside%%'
                                        THEN be.buying_side_gross_commission
                                    ELSE be.buying_side_gross_commission
                                END
                            ) = 0
                        THEN NULL

                        WHEN scn.officegrosscommissiononsale <> (
                            CASE
                                WHEN be.tags ILIKE '%%listingside%%' AND be.tags ILIKE '%%sellingside%%'
                                    THEN be.total_gross_commission
                                WHEN be.tags ILIKE '%%listingside%%'
                                    THEN be.listing_side_gross_commission
                                WHEN be.tags ILIKE '%%sellingside%%'
                                    THEN be.buying_side_gross_commission
                                ELSE be.buying_side_gross_commission
                            END
                        )
                        THEN 'mismatch'

                        ELSE 'match'
                    END AS gross_commission_mismatch
                FROM brokerage_engine be
                LEFT JOIN sale s ON s.saleguid = be.skyslopefileid
                LEFT JOIN sale_commission scn ON scn.saleguid = s.saleguid
                WHERE be.tags LIKE '%CdaSent%'
            )
            SELECT *
            FROM base
            ORDER BY transaction_id;
        """
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()
            reshaped_rows = []
            for row in rows:
                buyer_result = compare_names(
                    row["ss_buyer_name"],
                    row["be_buyer_name"]
                )
                seller_result = compare_names(
                    row["ss_seller_name"],
                    row["be_seller_name"]
                )
                listing_price_result = compare_listing_price(
                    row["be_listing_price"],
                    row["ss_listing_price"]
                )
                status_mismatch = row["transaction_status_mismatch"]
                gross_commission_mismatch = row["gross_commission_mismatch"]
                is_stale = (
                    row["sale_price_mismatch"]
                    or row["closed_date_mismatch"]
                    or row["contract_date_mismatch"]
                    or listing_price_result == "mismatch"
                    or status_mismatch is True
                    or buyer_result == "mismatch"
                    or seller_result == "mismatch"
                    or gross_commission_mismatch == "mismatch"
                )
                reshaped_row = {
                    "transaction_id": row["transaction_id"],
                    "skyslopefileid": row["skyslopefileid"],
                    "property_address": row["property_address"],
                    "tags": row["tags"],
                    "is_stale": is_stale,
                    "be_gross_commission": row["be_gross_commission"],
                    "ss_gross_commission": row["ss_gross_commission"],
                    "gross_commission_mismatch": gross_commission_mismatch,
                    "be_closed_date": row["be_closed_date"],
                    "ss_closed_date": row["ss_closed_date"],
                    "closed_date_mismatch": row["closed_date_mismatch"],
                    "be_sale_price": row["be_sale_price"],
                    "ss_sale_price": row["ss_sale_price"],
                    "sale_price_mismatch": row["sale_price_mismatch"],
                    "be_transaction_status": row["be_transaction_status"],
                    "ss_transaction_status": row["ss_transaction_status"],
                    "transaction_status_mismatch": status_mismatch,
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
            cur.execute("""
                SELECT COUNT(*) AS total_cda_sent
                FROM brokerage_engine be
                LEFT JOIN sale s ON s.saleguid = be.skyslopefileid
                WHERE be.tags LIKE '%CdaSent%'
            """)
            summary = cur.fetchone()
            cur.execute("""
                SELECT COUNT(*) AS no_skyslope_record
                FROM brokerage_engine be
                WHERE be.tags LIKE '%CdaSent%'
                AND be.skyslopefileid IS NULL
            """)
            no_skyslope = cur.fetchone()
        stale_count = sum(1 for row in reshaped_rows if row["is_stale"])
        return {
            "filter": filter,
            "total_cda_sent": summary["total_cda_sent"],
            "unmatched_count": stale_count,
            "no_skyslope_record": no_skyslope["no_skyslope_record"],
            "data": reshaped_rows,
        }
    finally:
        conn.close()