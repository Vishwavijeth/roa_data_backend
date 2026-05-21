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
                    LOWER(COALESCE(s.saleguid::text, '')) ILIKE %(search)s
                    OR LOWER(COALESCE(sp.streetaddress, '')) ILIKE %(search)s
                    OR LOWER(COALESCE(sp.county, '')) ILIKE %(search)s
                    OR LOWER(COALESCE(sp.state, '')) ILIKE %(search)s
                    OR LOWER(COALESCE(sp.zip, '')) ILIKE %(search)s
                    OR LOWER(COALESCE(r.firstname, '')) ILIKE %(search)s
                    OR LOWER(COALESCE(r.lastname, '')) ILIKE %(search)s
                )
            """
            search_params["search"] = f"%{search.lower()}%"

        # ─────────────────────────────────────────────
        # SKY SLOPE MODE
        # ─────────────────────────────────────────────
        if skyslope:
            shared_filters = ""
            params = {}

            if state:
                shared_filters += " AND LOWER(sp.state) = LOWER(%(state)s)"
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

            count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}

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
                    be.seller_name
                FROM brokerage_engine be
                LEFT JOIN sale s ON s.saleguid = be.skyslopefileid
            )
        """

        where_clause = " WHERE 1=1"
        params = {}

        if status != "all":
            where_clause += """
                AND (
                    CASE
                        WHEN LOWER(be_transaction_status) IN ('pending','active','in_progress') THEN 'pending'
                        WHEN LOWER(be_transaction_status) = 'closed' THEN 'closed'
                        WHEN LOWER(be_transaction_status) IN ('cancelled','canceled','canceled/app','canceled/pend') THEN 'cancelled'
                        ELSE 'other'
                    END = %(status)s
                )
            """
            params["status"] = status.lower()

        if state:
            where_clause += " AND LOWER(b.state) = LOWER(%(state)s)"
            params["state"] = state

        if transaction_specialist:
            where_clause += " AND LOWER(b.transaction_specialist) = LOWER(%(transaction_specialist)s)"
            params["transaction_specialist"] = transaction_specialist

        if from_close_date:
            where_clause += " AND b.be_closed_date >= %(from_close_date)s"
            params["from_close_date"] = from_close_date

        if to_close_date:
            where_clause += " AND b.be_closed_date <= %(to_close_date)s"
            params["to_close_date"] = to_close_date

        # ---------------- SEARCH (FULL MODE) ----------------
        if search:
            where_clause += """
                AND (
                    LOWER(COALESCE(b.transaction_id::text, '')) ILIKE %(search)s
                    OR LOWER(COALESCE(b.property_address, '')) ILIKE %(search)s
                    OR LOWER(COALESCE(b.state, '')) ILIKE %(search)s
                    OR LOWER(COALESCE(b.transaction_specialist, '')) ILIKE %(search)s
                    OR LOWER(COALESCE(b.buyer_name, '')) ILIKE %(search)s
                    OR LOWER(COALESCE(b.seller_name, '')) ILIKE %(search)s
                    OR LOWER(COALESCE(b.skyslopefileid::text, '')) ILIKE %(search)s
                )
            """
            params["search"] = f"%{search.lower()}%"

        count_query = base_cte + " SELECT COUNT(*) AS total FROM base b" + where_clause + ";"
        data_query = base_cte + " SELECT * FROM base b" + where_clause + " ORDER BY b.transaction_id LIMIT %(limit)s OFFSET %(offset)s;"

        params["limit"] = page_size
        params["offset"] = offset
        count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(count_query, count_params)
            total = cur.fetchone()["total"]

            cur.execute(data_query, params)
            rows = cur.fetchall()

        return {
            "mode": "full_comparison",
            "total": total,
            "data": rows,
        }

    finally:
        conn.close()