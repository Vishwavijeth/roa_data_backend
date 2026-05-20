from fastapi import APIRouter
from db import get_conn
from psycopg2.extras import RealDictCursor
from services.comparison import compare_names, compare_listing_price
from fastapi import Query

router = APIRouter()

@router.get("/month-closing/listing")
def get_month_closing(
    status: str = "all",
    skyslope: bool = False,
    state: str = None,
    from_close_date: str = None,
    to_close_date: str = None
):
    conn = get_conn()

    try:

        if skyslope:

            query = """
                SELECT
                    s.saleguid AS skyslopefileid,

                    s.saleprice AS ss_sale_price,
                    s.escrowclosingdate AS ss_closed_date,
                    s.contractacceptancedate AS ss_contract_date,
                    s.listingprice AS ss_listing_price,
                    s.status AS ss_transaction_status,

                    sp.state AS state,

                    CONCAT_WS(', ',
                        CONCAT_WS(' ',
                            sp.streetnumber,
                            sp.streetaddress,
                            sp.unit,
                            sp.direction
                        ),
                        sp.county,
                        sp.state,
                        sp.zip
                    ) AS property_address,

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
                    ) AS ss_seller_name

                FROM sale s
                LEFT JOIN brokerage_engine be
                    ON be.skyslopefileid = s.saleguid
                LEFT JOIN sale_property sp
                    ON sp.saleguid = s.saleguid
                WHERE be.skyslopefileid IS NULL
            """

            params = {}

            # STATE FILTER (SKYSLOPE → sp.state)
            if state:
                query += " AND LOWER(sp.state) = LOWER(%(state)s)"
                params["state"] = state

            # CLOSE DATE FILTER (SKYSLOPE → s.escrowclosingdate)
            if from_close_date:
                query += " AND s.escrowclosingdate >= %(from_close_date)s"
                params["from_close_date"] = from_close_date

            if to_close_date:
                query += " AND s.escrowclosingdate <= %(to_close_date)s"
                params["to_close_date"] = to_close_date

            query += " ORDER BY s.saleguid;"

            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, params)
                rows = cur.fetchall()

            return {
                "mode": "skyslope_only",
                "count": len(rows),
                "data": rows
            }

        query = """
            WITH base AS (
                SELECT
                    be.transaction_identifier_transactionid AS transaction_id,
                    be.skyslopefileid,
                    be.property_address,
                    be.tags,
                    be.state,

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

                    be.total_gross_commission AS be_gross_commission,
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

                    be.buyer_name AS be_buyer_name,
                    be.seller_name AS be_seller_name,

                    CASE
                        WHEN be.sale_price IS DISTINCT FROM s.saleprice
                        THEN true ELSE false
                    END AS sale_price_mismatch,

                    CASE
                        WHEN be.closed_date IS DISTINCT FROM s.escrowclosingdate
                        THEN true ELSE false
                    END AS closed_date_mismatch,

                    CASE
                        WHEN be.contract_date IS DISTINCT FROM s.contractacceptancedate
                        THEN true ELSE false
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
                          OR be.total_gross_commission IS NULL
                          OR scn.officegrosscommissiononsale = 0
                          OR be.total_gross_commission = 0
                        THEN NULL
                        WHEN scn.officegrosscommissiononsale <> be.total_gross_commission
                        THEN 'mismatch'
                        ELSE 'match'
                    END AS gross_commission_mismatch

                FROM brokerage_engine be
                LEFT JOIN sale s ON s.saleguid = be.skyslopefileid
                LEFT JOIN sale_commission scn ON scn.saleguid = s.saleguid
            )
            SELECT *
            FROM base b
            WHERE 1=1
        """

        params = {}

        # STATUS FILTER
        if status != "all":
            query += """
                AND (
                    CASE
                        WHEN LOWER(be_transaction_status) IN ('pending', 'active', 'in_progress')
                            THEN 'pending'
                        WHEN LOWER(be_transaction_status) = 'closed'
                            THEN 'closed'
                        WHEN LOWER(be_transaction_status) IN ('cancelled', 'canceled', 'canceled/app', 'canceled/pend')
                            THEN 'cancelled'
                        ELSE 'other'
                    END = %(status)s
                )
            """
            params["status"] = status.lower()

        # STATE FILTER (NORMAL → be.state)
        if state:
            query += " AND LOWER(state) = LOWER(%(state)s)"
            params["state"] = state

        # CLOSE DATE FILTER (NORMAL → be.closed_date)
        if from_close_date:
            query += " AND b.be_closed_date >= %(from_close_date)s"
            params["from_close_date"] = from_close_date

        if to_close_date:
            query += " AND b.be_closed_date <= %(to_close_date)s"
            params["to_close_date"] = to_close_date

        query += " ORDER BY b.transaction_id;"

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

            reshaped_rows = []

            for row in rows:

                buyer_result = compare_names(
                    row["ss_buyer_name"],
                    row["be_buyer_name"]
                )

                listing_price_result = compare_listing_price(
                    row["be_listing_price"],
                    row["ss_listing_price"]
                )

                reshaped_rows.append({
                    "transaction_id": row["transaction_id"],
                    "skyslopefileid": row["skyslopefileid"],
                    "property_address": row["property_address"],
                    "tags": row["tags"],
                    "state": row["state"],

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
                    "listing_price_comparison": listing_price_result,

                    "be_gross_commission": row["be_gross_commission"],
                    "ss_gross_commission": row["ss_gross_commission"],
                    "gross_commission_mismatch": row["gross_commission_mismatch"],

                    "be_transaction_status": row["be_transaction_status"],
                    "ss_transaction_status": row["ss_transaction_status"],
                    "transaction_status_mismatch": row["transaction_status_mismatch"],

                    "be_buyer_name": row["be_buyer_name"],
                    "ss_buyer_name": row["ss_buyer_name"],
                    "buyer_name_comparison": buyer_result,
                })

        return {
            "mode": "full_comparison" if not skyslope else "skyslope_only",
            "count": len(rows),
            "data": reshaped_rows if not skyslope else rows
        }

    finally:
        conn.close()