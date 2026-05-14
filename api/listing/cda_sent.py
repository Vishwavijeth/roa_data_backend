from fastapi import APIRouter
from db import get_conn
from psycopg2.extras import RealDictCursor
from services.comparison import compare_names
from fastapi import Query


router = APIRouter()

def compare_listing_price(be_price, ss_price):
    """
    - If either side is None/null → return 'null' (indeterminate)
    - Otherwise compare numerically → return 'match' or 'mismatch'
    """
    if be_price is None or ss_price is None:
        return 'null'
    return 'match' if float(be_price) == float(ss_price) else 'mismatch'

@router.get("/cda-sent/listing")
def get_cda_sent(filter: str = Query("all", enum=["all", "mismatch"])):
    conn = get_conn()

    try:
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
                        WHEN be.transaction_status IS NULL OR s.status IS NULL THEN NULL
                        WHEN LOWER(be.transaction_status) = LOWER(s.status) THEN false
                        WHEN LOWER(be.transaction_status) = 'cancelled'
                             AND LOWER(s.status) IN ('canceled/app', 'cancelled/pend')
                        THEN false
                        ELSE true
                    END AS transaction_status_mismatch

                FROM brokerage_engine be
                LEFT JOIN sale s ON s.saleguid = be.skyslopefileid
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

                is_stale = (
                    row["sale_price_mismatch"]
                    or row["closed_date_mismatch"]
                    or row["contract_date_mismatch"]
                    or listing_price_result == "mismatch"
                    or status_mismatch is True
                    or buyer_result == "mismatch"
                    or seller_result == "mismatch"
                )

                reshaped_row = {
                    "transaction_id": row["transaction_id"],
                    "skyslopefileid": row["skyslopefileid"],
                    "property_address": row["property_address"],
                    "tags": row["tags"],
                    "is_stale": is_stale,

                    "be_sale_price": row["be_sale_price"],
                    "ss_sale_price": row["ss_sale_price"],
                    "sale_price_mismatch": row["sale_price_mismatch"],

                    "be_closed_date": row["be_closed_date"],
                    "ss_closed_date": row["ss_closed_date"],
                    "closed_date_mismatch": row["closed_date_mismatch"],

                    "be_contract_date": row["be_contract_date"],
                    "ss_contract_date": row["ss_contract_date"],
                    "contract_date_mismatch": row["contract_date_mismatch"],

                    "be_listing_price": row["be_listing_price"],
                    "ss_listing_price": row["ss_listing_price"],
                    "listing_price_mismatch": listing_price_result,

                    "be_transaction_status": row["be_transaction_status"],
                    "ss_transaction_status": row["ss_transaction_status"],
                    "transaction_status_mismatch": status_mismatch,

                    "be_buyer_name": row["be_buyer_name"],
                    "ss_buyer_name": row["ss_buyer_name"],
                    "buyer_name_comparison": buyer_result,

                    "be_seller_name": row["be_seller_name"],
                    "ss_seller_name": row["ss_seller_name"],
                    "seller_name_comparison": seller_result,
                }

                # Apply filter
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

        stale_count = sum(1 for row in reshaped_rows if row["is_stale"])

        return {
            "filter": filter,
            "total_cda_sent": summary["total_cda_sent"],
            "unmatched_count": stale_count,
            "data": reshaped_rows,
        }

    finally:
        conn.close()