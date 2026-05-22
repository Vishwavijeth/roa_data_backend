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
    to_close_date: str = None,
    transaction_specialist: str = None,
    search: str = None,
    page: int = 1,
    page_size: int = 50,
):
    conn = get_conn()
    offset = (page - 1) * page_size

    try:
        search_clause = ""
        search_params = {}

        # ---------------- SEARCH FILTER ----------------
        if search:
            search_clause = """
                AND (
                    COALESCE(s.saleguid::text, '') ILIKE %(search)s
                    OR COALESCE(sp.streetaddress, '') ILIKE %(search)s
                    OR COALESCE(sp.county, '') ILIKE %(search)s
                    OR COALESCE(sp.state, '') ILIKE %(search)s
                    OR COALESCE(sp.zip, '') ILIKE %(search)s
                    OR COALESCE(r.firstname, '') ILIKE %(search)s
                    OR COALESCE(r.lastname, '') ILIKE %(search)s
                )
            """
            search_params["search"] = f"%{search}%"

        # ─────────────────────────────────────────────
        # SKY SLOPE MODE
        # ─────────────────────────────────────────────
        if skyslope:
            shared_filters = ""
            params = {}

            if state:
                shared_filters += " AND sp.state ILIKE %(state)s"
                params["state"] = state

            if from_close_date:
                shared_filters += " AND s.escrowclosingdate >= %(from_close_date)s"
                params["from_close_date"] = from_close_date

            if to_close_date:
                shared_filters += " AND s.escrowclosingdate <= %(to_close_date)s"
                params["to_close_date"] = to_close_date

            base_from = """
                FROM sale s
                LEFT JOIN brokerage_engine be ON be.skyslopefileid = s.saleguid
                LEFT JOIN users r             ON s.reviewerguid = r.userguid
                LEFT JOIN sale_property sp    ON sp.saleguid = s.saleguid
                WHERE be.skyslopefileid IS NULL
            """

            base_from += search_clause

            count_query = "SELECT COUNT(*) AS total " + base_from + shared_filters + ";"

            data_query = """
                SELECT
                    s.saleguid AS skyslopefileid,
                    s.saleprice AS ss_sale_price,
                    s.status AS ss_status,
                    s.escrowclosingdate AS ss_closed_date,
                    s.contractacceptancedate AS ss_contract_date,
                    s.listingprice AS ss_listing_price,
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
                    COALESCE(r.firstname || ' ' || r.lastname, '') AS reviewer
            """ + base_from + shared_filters + """
                ORDER BY s.saleguid
                LIMIT %(limit)s OFFSET %(offset)s;
            """

            params.update(search_params)
            params["limit"] = page_size
            params["offset"] = offset

            count_params = {
                k: v for k, v in params.items()
                if k not in ("limit", "offset")
            }

            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(count_query, count_params)
                total = cur.fetchone()["total"]

                cur.execute(data_query, params)
                rows = cur.fetchall()

            return {
                "mode": "skyslope_only",
                "total": total,
                "data": rows,
            }

        # ─────────────────────────────────────────────
        # FULL COMPARISON MODE
        # ─────────────────────────────────────────────
        base_cte = """
            WITH base AS (
                SELECT
                    be.transaction_identifier_transactionid AS transaction_id,
                    be.skyslopefileid,
                    be.property_address,
                    be.state,
                    be.transaction_specialist,
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
                    be.buyer_name,
                    be.seller_name,
                    scn.officegrosscommissiononsale AS ss_gross_commission,
                    be.total_gross_commission AS be_gross_commission,

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

                    CASE
                        WHEN be.sale_price IS DISTINCT FROM s.saleprice THEN 'mismatch'
                        ELSE 'match'
                    END AS sale_price_comparison,

                    CASE
                        WHEN be.closed_date IS DISTINCT FROM s.escrowclosingdate THEN 'mismatch'
                        ELSE 'match'
                    END AS closed_date_comparison,

                    CASE
                        WHEN be.contract_date IS DISTINCT FROM s.contractacceptancedate THEN 'mismatch'
                        ELSE 'match'
                    END AS contract_date_comparison,

                    CASE
                        WHEN LOWER(s.status) = 'expired' THEN NULL
                        WHEN be.transaction_status IS NULL OR s.status IS NULL THEN NULL
                        WHEN LOWER(be.transaction_status) = 'closed'
                            AND LOWER(s.status) = 'archived'
                        THEN 'match'
                        WHEN LOWER(be.transaction_status) = LOWER(s.status) THEN 'match'
                        WHEN LOWER(be.transaction_status) = 'cancelled'
                             AND LOWER(s.status) IN ('canceled/app', 'canceled/pend')
                        THEN 'match'
                        ELSE 'mismatch'
                    END AS transaction_status_comparison,

                    CASE
                        WHEN scn.officegrosscommissiononsale IS NULL
                            OR be.total_gross_commission IS NULL
                            OR scn.officegrosscommissiononsale = 0
                            OR be.total_gross_commission = 0
                        THEN NULL
                        WHEN scn.officegrosscommissiononsale <> be.total_gross_commission
                        THEN 'mismatch'
                        ELSE 'match'
                    END AS gross_commission_comparison
                FROM brokerage_engine be
                LEFT JOIN sale s ON s.saleguid = be.skyslopefileid
                LEFT JOIN sale_commission scn ON scn.saleguid = s.saleguid
            )
        """

        where_clause = " WHERE 1=1"
        params = {}

        # ---------------- STATUS FILTER ----------------
        if status != "all":
            where_clause += """
                AND (
                    CASE
                        WHEN be_transaction_status ILIKE 'pending'
                             OR be_transaction_status ILIKE 'active'
                             OR be_transaction_status ILIKE 'in_progress'
                        THEN 'pending'
                        WHEN be_transaction_status ILIKE 'closed'
                        THEN 'closed'
                        WHEN be_transaction_status ILIKE 'cancelled'
                             OR be_transaction_status ILIKE 'canceled'
                             OR be_transaction_status ILIKE 'canceled/app'
                             OR be_transaction_status ILIKE 'canceled/pend'
                        THEN 'cancelled'
                        ELSE 'other'
                    END = %(status)s
                )
            """
            params["status"] = status

        # ---------------- STATE FILTER ----------------
        if state:
            where_clause += " AND b.state ILIKE %(state)s"
            params["state"] = state

        # ---------------- TRANSACTION SPECIALIST ----------------
        if transaction_specialist:
            if transaction_specialist.lower() == "unassigned":
                where_clause += """
                    AND (
                        b.transaction_specialist IS NULL
                        OR b.transaction_specialist = ''
                    )
                """
            else:
                where_clause += """
                    AND b.transaction_specialist = %(transaction_specialist)s
                """
                params["transaction_specialist"] = transaction_specialist

        # ---------------- DATE FILTER ----------------
        if from_close_date:
            where_clause += " AND b.be_closed_date >= %(from_close_date)s"
            params["from_close_date"] = from_close_date

        if to_close_date:
            where_clause += " AND b.be_closed_date <= %(to_close_date)s"
            params["to_close_date"] = to_close_date

        # ---------------- SEARCH FILTER ----------------
        if search:
            where_clause += """
                AND (
                    COALESCE(b.transaction_id::text, '') ILIKE %(search)s
                    OR COALESCE(b.property_address, '') ILIKE %(search)s
                    OR COALESCE(b.state, '') ILIKE %(search)s
                    OR COALESCE(b.transaction_specialist, '') ILIKE %(search)s
                    OR COALESCE(b.buyer_name, '') ILIKE %(search)s
                    OR COALESCE(b.seller_name, '') ILIKE %(search)s
                    OR COALESCE(b.skyslopefileid::text, '') ILIKE %(search)s
                )
            """
            params["search"] = f"%{search}%"

        count_query = base_cte + " SELECT COUNT(*) AS total FROM base b" + where_clause + ";"

        data_query = (
            base_cte
            + " SELECT * FROM base b"
            + where_clause
            + " ORDER BY b.transaction_id"
            + " LIMIT %(limit)s OFFSET %(offset)s;"
        )

        params["limit"] = page_size
        params["offset"] = offset

        count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(count_query, count_params)
            total = cur.fetchone()["total"]

            cur.execute(data_query, params)
            rows = cur.fetchall()

        # ---------------- NAME COMPARISON ADDITION ----------------
        for row in rows:
            row["buyer_name_comparison"] = compare_names(
                row.get("buyer_name"),
                row.get("ss_buyer_name")
            )
            row["seller_name_comparison"] = compare_names(
                row.get("seller_name"),
                row.get("ss_seller_name")
            )
            row["listing_price_comparison"] = compare_listing_price(
                row.get("be_listing_price"),
                row.get("ss_listing_price")
            )

        return {
            "mode": "full_comparison",
            "total": total,
            "data": rows,
        }

    finally:
        conn.close()