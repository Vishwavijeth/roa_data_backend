from fastapi import APIRouter, Query
from typing import List
from db import get_conn
from psycopg2.extras import RealDictCursor
from services.comparison import compare_names, compare_listing_price
from fastapi import Query

router = APIRouter()


@router.get("/month-closing/listing")
def get_month_closing(
    status: str = "all",
    skyslope: bool = False,
    state: List[str] = Query(default=[]),
    from_close_date: str = None,
    to_close_date: str = None,
    transaction_specialist: List[str] = Query(default=[]),
    search: str = None,
    mismatch: bool = False,
    pending_subfilter: List[str] = Query(default=[]),
    page: int = 1,
    page_size: int = 50,
):
    conn = get_conn()
    offset = (page - 1) * page_size
    try:
        search_clause = ""
        search_params = {}

        # ── parse multi-value params (supports both ?x=A,B and ?x=A&x=B) ──
        state_list = [v.strip() for s in state             for v in s.split(",") if v.strip()]
        ts_list    = [v.strip() for s in transaction_specialist for v in s.split(",") if v.strip()]
        ps_list    = [v.strip() for s in pending_subfilter  for v in s.split(",") if v.strip()]
        # ──────────────────────────────────────────────────────────────────

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

            if state_list:
                placeholders = ", ".join(f"%(state_{i})s" for i in range(len(state_list)))
                shared_filters += f" AND LOWER(sp.state) IN ({placeholders})"
                for i, v in enumerate(state_list):
                    params[f"state_{i}"] = v.lower()

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

            return {"mode": "skyslope_only", "total": total, "data": rows}

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
                    CASE
                        WHEN be.tags ILIKE '%%listingside%%' AND be.tags ILIKE '%%sellingside%%'
                            THEN be.total_gross_commission
                        WHEN be.tags ILIKE '%%listingside%%'
                            THEN be.listing_side_gross_commission
                        WHEN be.tags ILIKE '%%sellingside%%'
                            THEN be.buying_side_gross_commission
                        ELSE be.buying_side_gross_commission
                    END AS be_gross_commission,
                    CASE
                        WHEN be.tags ILIKE '%%titlepaymentreceived%%' THEN 'titlepaymentreceived'
                        WHEN be.tags ILIKE '%%commissionverified%%'   THEN 'commissionverified'
                        WHEN be.tags ILIKE '%%cdasent%%'              THEN 'cdasent'
                        WHEN be.tags ILIKE '%%complete%%'             THEN 'complete'
                        WHEN be.tags ILIKE '%%open%%'                 THEN 'open'
                        ELSE NULL
                    END AS be_stage,
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
                        WHEN be.sale_price IS NULL OR s.saleprice IS NULL THEN NULL
                        WHEN be.sale_price IS DISTINCT FROM s.saleprice   THEN 'mismatch'
                        ELSE 'match'
                    END AS sale_price_comparison,
                    CASE
                        WHEN be.closed_date IS NULL OR s.escrowclosingdate IS NULL THEN NULL
                        WHEN be.closed_date IS DISTINCT FROM s.escrowclosingdate   THEN 'mismatch'
                        ELSE 'match'
                    END AS closed_date_comparison,
                    CASE
                        WHEN be.contract_date IS NULL OR s.contractacceptancedate IS NULL THEN NULL
                        WHEN be.contract_date IS DISTINCT FROM s.contractacceptancedate   THEN 'mismatch'
                        ELSE 'match'
                    END AS contract_date_comparison,
                    CASE
                        WHEN be.transaction_status IS NULL OR TRIM(be.transaction_status) = ''
                          OR s.status IS NULL            OR TRIM(s.status) = ''
                        THEN NULL
                        WHEN LOWER(s.status) = 'expired' THEN NULL
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

        # ---------------- PENDING SUB-FILTER (multi) ----------------
        if ps_list:
            placeholders = ", ".join(f"%(ps_{i})s" for i in range(len(ps_list)))
            where_clause += f" AND b.be_stage IN ({placeholders})"
            for i, v in enumerate(ps_list):
                params[f"ps_{i}"] = v

        # ---------------- STATE FILTER (multi) ----------------
        if state_list:
            placeholders = ", ".join(f"%(state_{i})s" for i in range(len(state_list)))
            where_clause += f" AND LOWER(b.state) IN ({placeholders})"
            for i, v in enumerate(state_list):
                params[f"state_{i}"] = v.lower()

        # ---------------- TRANSACTION SPECIALIST FILTER (multi) ----------------
        if ts_list:
            unassigned_requested = any(v.lower() == "unassigned" for v in ts_list)
            named = [v for v in ts_list if v.lower() != "unassigned"]

            if unassigned_requested and named:
                placeholders = ", ".join(f"%(ts_{i})s" for i in range(len(named)))
                where_clause += f"""
                    AND (
                        b.transaction_specialist IS NULL
                        OR b.transaction_specialist = ''
                        OR b.transaction_specialist IN ({placeholders})
                    )
                """
                for i, v in enumerate(named):
                    params[f"ts_{i}"] = v
            elif unassigned_requested:
                where_clause += """
                    AND (
                        b.transaction_specialist IS NULL
                        OR b.transaction_specialist = ''
                    )
                """
            else:
                placeholders = ", ".join(f"%(ts_{i})s" for i in range(len(named)))
                where_clause += f" AND b.transaction_specialist IN ({placeholders})"
                for i, v in enumerate(named):
                    params[f"ts_{i}"] = v

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
        data_query  = (
            base_cte
            + " SELECT * FROM base b"
            + where_clause
            + " ORDER BY b.transaction_id"
            + " LIMIT %(limit)s OFFSET %(offset)s;"
        )
        params["limit"]  = page_size
        params["offset"] = offset
        count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(count_query, count_params)
            total = cur.fetchone()["total"]
            cur.execute(data_query, params)
            rows = cur.fetchall()

        # ---------------- POST PROCESSING ----------------
        for row in rows:
            row["buyer_name_comparison"]    = compare_names(row.get("buyer_name"),       row.get("ss_buyer_name"))
            row["seller_name_comparison"]   = compare_names(row.get("seller_name"),      row.get("ss_seller_name"))
            row["listing_price_comparison"] = compare_listing_price(row.get("be_listing_price"), row.get("ss_listing_price"))

        # ---------------- MISMATCH FILTER ----------------
        if mismatch:
            def has_mismatch(r):
                return any(
                    r.get(k) == "mismatch" for k in (
                        "sale_price_comparison", "closed_date_comparison",
                        "contract_date_comparison", "transaction_status_comparison",
                        "gross_commission_comparison", "buyer_name_comparison",
                        "seller_name_comparison", "listing_price_comparison",
                    )
                )
            rows  = [r for r in rows if has_mismatch(r)]
            total = len(rows)

        return {"mode": "full_comparison", "total": total, "data": rows}

    finally:
        conn.close()