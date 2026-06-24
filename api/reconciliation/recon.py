from typing import Optional, List
from fastapi import APIRouter, Query, Depends, HTTPException
from psycopg2.extras import RealDictCursor
from db import get_db
from services.comparison import compare_names, compare_buying_agent

router = APIRouter()

BASE_QUERY = """
WITH brokerage_base AS (
    SELECT
        'brokerage_engine'::text AS source_table,
        be.skyslopefileid AS skyslopefileid,
        be.transaction_identifier_transactionid::text AS transactionid,
        be.property_address::text AS propertyaddress,
        be.transaction_status::text AS transaction_status,
        be.tags::text AS tags,
        be.buyer_name::text AS be_buyer_name,
        be.seller_name::text AS be_seller_name,
        be.buying_agent_name::text AS be_buying_agent_name,
        be.da_title_company::text AS be_title_company,
        be.sale_price::numeric AS be_sale_price,
        be.listing_price::numeric AS be_listing_price,
        be.closed_date::date AS be_close_date,
        CASE
            WHEN be.tags ILIKE '%%listingside%%' AND be.tags ILIKE '%%sellingside%%'
                THEN be.total_gross_commission
            WHEN be.tags ILIKE '%%listingside%%'
                THEN be.listing_side_gross_commission
            WHEN be.tags ILIKE '%%sellingside%%'
                THEN be.buying_side_gross_commission
            ELSE be.buying_side_gross_commission
        END::numeric AS be_gross_commission
    FROM brokerage_engine be
),
other_income_base AS (
    SELECT
        'otherincome_transactions'::text AS source_table,
        oit.skyslopefileid AS skyslopefileid,
        oit.transaction_identifier_transactionid::text AS transactionid,
        oit.property_address::text AS propertyaddress,
        oit.transaction_status::text AS transaction_status,
        oit.tags::text AS tags,
        NULL::text AS be_buyer_name,
        NULL::text AS be_seller_name,
        NULL::text AS be_buying_agent_name,
        NULL::text AS be_title_company,
        oit.income_received::numeric AS be_sale_price,
        NULL::numeric AS be_listing_price,
        oit.income_received_date::date AS be_close_date,
        oit.gross_commission::numeric AS be_gross_commission
    FROM otherincome_transactions oit
),
combined_source AS (
    SELECT * FROM brokerage_base
    UNION ALL
    SELECT * FROM other_income_base
),
latest_review AS (
    SELECT DISTINCT ON (rr.transactionid)
        rr.transactionid::text AS transactionid,
        rr.review_status,
        rr.notes,
        rr.updated_by,
        rr.updated_at
    FROM reconciliation_review rr
    ORDER BY rr.transactionid, rr.updated_at DESC
)
SELECT
    cs.source_table,
    cs.skyslopefileid,
    s.saleguid,
    cs.transactionid,
    cs.propertyaddress,
    cs.transaction_status AS be_status,
    s.status AS skyslope_status,
    st.name AS skyslope_stage,
    cs.tags,
    cs.be_buyer_name,
    cs.be_seller_name,
    cs.be_buying_agent_name,
    cs.be_title_company,
    cs.be_sale_price,
    cs.be_listing_price,
    cs.be_close_date,
    cs.be_gross_commission,
    CASE WHEN s.saleguid IS NOT NULL THEN
        COALESCE(
            (
                SELECT STRING_AGG(TRIM(COALESCE(sc.firstname, '') || ' ' || COALESCE(sc.lastname, '')), ', ')
                FROM sale_contact sc
                WHERE sc.saleguid = s.saleguid AND LOWER(sc.role) = 'buyer'
            ), ''
        )
    ELSE '' END AS skyslope_buyer_name,
    CASE WHEN s.saleguid IS NOT NULL THEN
        COALESCE(
            (
                SELECT STRING_AGG(TRIM(COALESCE(sc.firstname, '') || ' ' || COALESCE(sc.lastname, '')), ', ')
                FROM sale_contact sc
                WHERE sc.saleguid = s.saleguid AND LOWER(sc.role) = 'seller'
            ), ''
        )
    ELSE '' END AS skyslope_seller_name,
    CASE WHEN s.saleguid IS NOT NULL THEN
        COALESCE(
            (
                SELECT TRIM(COALESCE(uu.firstname, '') || ' ' || COALESCE(uu.lastname, ''))
                FROM users uu
                WHERE uu.userguid = s.agentguid
            ), ''
        )
    ELSE '' END AS skyslope_buying_agent_name,
    CASE WHEN s.saleguid IS NOT NULL THEN
        COALESCE(
            (
                SELECT sc.company
                FROM sale_contact sc
                WHERE sc.saleguid = s.saleguid AND LOWER(sc.role) = 'titlecompany'
                LIMIT 1
            ), ''
        )
    ELSE '' END AS skyslope_title_company,
    s.saleprice::numeric AS skyslope_sale_price,
    s.listingprice::numeric AS skyslope_listing_price,
    s.escrowclosingdate::date AS skyslope_close_date,
    scn.officeGrossCommissionOnSale::numeric AS officegrosscommissiononsale,
    scn.listingcommissionamount::numeric AS listingcommissionamount,
    scn.salecommissionamount::numeric AS salecommissionamount,
    lr.review_status,
    lr.notes AS review_notes,
    lr.updated_by AS review_updated_by,
    lr.updated_at AS review_updated_at
FROM combined_source cs
LEFT JOIN sale s ON s.saleguid = cs.skyslopefileid
LEFT JOIN stage st ON st.stageid = s.stageid
LEFT JOIN sale_commission scn ON scn.saleguid = s.saleguid
LEFT JOIN latest_review lr ON lr.transactionid = cs.transactionid
"""


PARAMETER_DISPLAY_NAMES = {
    "gross_commission": "Gross Commission",
    "close_date": "Close Date",
    "status": "Status",
    "sale_price": "Sale Price",
    "listing_price": "Listing Price",
    "buyer_name": "Buyer Name",
    "seller_name": "Seller Name",
    "buying_agent_name": "Buying Agent Name",
    "title_company": "Title Company",
}

SOURCE_TABLE_DISPLAY_NAMES = {
    "brokerage_engine": "sale income",
    "otherincome_transactions": "other income",
}

SOURCE_TABLE_FILTER_MAP = {
    "sale income": "brokerage_engine",
    "other income": "otherincome_transactions",
}


NOT_CANCELLED = """
NOT (
    cs.be_status ILIKE 'cancelled'
    AND cs.skyslope_status ILIKE ANY (ARRAY['canceled/pend', 'canceled/app'])
)
"""


MISMATCH_SQL_FILTERS = {
    "close_date": f"""
        (
            cs.saleguid IS NOT NULL
            AND {NOT_CANCELLED}
            AND cs.be_close_date IS NOT NULL
            AND cs.skyslope_close_date IS NOT NULL
            AND cs.be_close_date != cs.skyslope_close_date
        )
    """,
    "sale_price": f"""
        (
            cs.saleguid IS NOT NULL
            AND {NOT_CANCELLED}
            AND cs.be_sale_price IS NOT NULL
            AND cs.skyslope_sale_price IS NOT NULL
            AND cs.be_sale_price != cs.skyslope_sale_price
        )
    """,
    "listing_price": f"""
        (
            cs.source_table = 'brokerage_engine'
            AND cs.saleguid IS NOT NULL
            AND {NOT_CANCELLED}
            AND cs.be_listing_price IS NOT NULL
            AND cs.skyslope_listing_price IS NOT NULL
            AND cs.be_listing_price != 0
            AND cs.skyslope_listing_price != 0
            AND cs.be_listing_price != cs.skyslope_listing_price
        )
    """,
    "status": f"""
        (
            cs.saleguid IS NOT NULL
            AND {NOT_CANCELLED}
            AND (
                cs.be_status IS NULL
                OR cs.skyslope_status IS NULL
                OR (
                    NOT (cs.be_status ILIKE 'closed' AND cs.skyslope_status ILIKE ANY (ARRAY['archived', 'closed']))
                    AND NOT (cs.be_status ILIKE cs.skyslope_status)
                    AND NOT (cs.be_status ILIKE 'pending' AND cs.skyslope_status ILIKE 'expired')
                )
            )
        )
    """,
    "buyer_name": f"""
        (
            cs.source_table = 'brokerage_engine'
            AND cs.saleguid IS NOT NULL
            AND {NOT_CANCELLED}
            AND cs.be_buyer_name IS NOT NULL
            AND TRIM(cs.be_buyer_name) != ''
            AND cs.skyslope_buyer_name IS NOT NULL
            AND TRIM(cs.skyslope_buyer_name) != ''
            AND LOWER(TRIM(cs.be_buyer_name)) != LOWER(TRIM(cs.skyslope_buyer_name))
        )
    """,
    "seller_name": f"""
        (
            cs.source_table = 'brokerage_engine'
            AND cs.saleguid IS NOT NULL
            AND {NOT_CANCELLED}
            AND cs.be_seller_name IS NOT NULL
            AND TRIM(cs.be_seller_name) != ''
            AND cs.skyslope_seller_name IS NOT NULL
            AND TRIM(cs.skyslope_seller_name) != ''
            AND LOWER(TRIM(cs.be_seller_name)) != LOWER(TRIM(cs.skyslope_seller_name))
        )
    """,
    "buying_agent_name": f"""
        (
            cs.source_table = 'brokerage_engine'
            AND cs.saleguid IS NOT NULL
            AND {NOT_CANCELLED}
            AND cs.be_buying_agent_name IS NOT NULL
            AND TRIM(cs.be_buying_agent_name) != ''
            AND cs.skyslope_buying_agent_name IS NOT NULL
            AND TRIM(cs.skyslope_buying_agent_name) != ''
            AND LOWER(TRIM(cs.be_buying_agent_name)) != LOWER(TRIM(cs.skyslope_buying_agent_name))
        )
    """,
    "title_company": f"""
        (
            cs.source_table = 'brokerage_engine'
            AND cs.saleguid IS NOT NULL
            AND {NOT_CANCELLED}
            AND cs.be_title_company IS NOT NULL
            AND TRIM(cs.be_title_company) != ''
            AND cs.skyslope_title_company IS NOT NULL
            AND TRIM(cs.skyslope_title_company) != ''
            AND LOWER(TRIM(cs.be_title_company)) != LOWER(TRIM(cs.skyslope_title_company))
        )
    """,
    "gross_commission": f"""
        (
            cs.saleguid IS NOT NULL
            AND {NOT_CANCELLED}
            AND cs.be_gross_commission IS NOT NULL
            AND cs.be_gross_commission != 0
            AND CASE
                WHEN cs.tags ILIKE '%%listingside%%' AND cs.tags ILIKE '%%sellingside%%' THEN cs.officegrosscommissiononsale
                WHEN cs.tags ILIKE '%%listingside%%' THEN COALESCE(cs.listingcommissionamount, cs.officegrosscommissiononsale)
                WHEN cs.tags ILIKE '%%sellingside%%' THEN COALESCE(cs.salecommissionamount, cs.officegrosscommissiononsale)
                ELSE COALESCE(cs.salecommissionamount, cs.officegrosscommissiononsale)
            END IS NOT NULL
            AND CASE
                WHEN cs.tags ILIKE '%%listingside%%' AND cs.tags ILIKE '%%sellingside%%' THEN cs.officegrosscommissiononsale
                WHEN cs.tags ILIKE '%%listingside%%' THEN COALESCE(cs.listingcommissionamount, cs.officegrosscommissiononsale)
                WHEN cs.tags ILIKE '%%sellingside%%' THEN COALESCE(cs.salecommissionamount, cs.officegrosscommissiononsale)
                ELSE COALESCE(cs.salecommissionamount, cs.officegrosscommissiononsale)
            END != 0
            AND ROUND(cs.be_gross_commission, 2) != ROUND(
                CASE
                    WHEN cs.tags ILIKE '%%listingside%%' AND cs.tags ILIKE '%%sellingside%%' THEN cs.officegrosscommissiononsale
                    WHEN cs.tags ILIKE '%%listingside%%' THEN COALESCE(cs.listingcommissionamount, cs.officegrosscommissiononsale)
                    WHEN cs.tags ILIKE '%%sellingside%%' THEN COALESCE(cs.salecommissionamount, cs.officegrosscommissiononsale)
                    ELSE COALESCE(cs.salecommissionamount, cs.officegrosscommissiononsale)
                END,
                2
            )
        )
    """,
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


def build_where_clause(
    search: Optional[str],
    parsed_mismatch_params: List[str],
    parsed_source_tables: Optional[List[str]] = None,
):
    conditions = []
    params = []

    if search:
        conditions.append("""
            (
                cs.transactionid ILIKE %s
                OR cs.propertyaddress ILIKE %s
                OR CAST(cs.skyslopefileid AS TEXT) ILIKE %s
            )
        """)
        search_term = f"%{search}%"
        params.extend([search_term, search_term, search_term])

    if parsed_source_tables:
        conditions.append("cs.source_table = ANY(%s)")
        params.append(parsed_source_tables)

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


def compare_names_fast(be_val, ss_val):
    if be_val is None or ss_val is None:
        return "null"

    be_clean = be_val.strip().lower()
    ss_clean = ss_val.strip().lower()

    if be_clean == ss_clean:
        return "match"

    if be_clean == "" or ss_clean == "":
        return "null"

    return compare_names(be_val, ss_val)


def compare_buying_agent_fast(be_val, ss_val):
    if be_val is None or ss_val is None:
        return "null"

    be_clean = be_val.strip().lower()
    ss_clean = ss_val.strip().lower()

    if be_clean == ss_clean:
        return "match"

    if be_clean == "" or ss_clean == "":
        return "null"

    return compare_buying_agent(be_val, ss_val)


def evaluate_row(row):
    source_table = row.get("source_table")
    saleguid = row.get("saleguid")
    be_status = row.get("be_status")
    skyslope_status = row.get("skyslope_status")
    tags = (row.get("tags") or "").lower()

    is_cancelled = False
    if be_status and be_status.lower() == "cancelled":
        if skyslope_status and skyslope_status.lower() in ["canceled/pend", "canceled/app"]:
            is_cancelled = True

    be_gci = row.get("be_gross_commission")
    office_gci = row.get("officegrosscommissiononsale")
    listing_comm = row.get("listingcommissionamount")
    sale_comm = row.get("salecommissionamount")

    if "listingside" in tags and "sellingside" in tags:
        ss_gci = office_gci
    elif "listingside" in tags:
        ss_gci = listing_comm if listing_comm is not None else office_gci
    elif "sellingside" in tags:
        ss_gci = sale_comm if sale_comm is not None else office_gci
    else:
        ss_gci = sale_comm if sale_comm is not None else office_gci

    gci_result = None
    if saleguid is None:
        gci_result = "no_skyslope_record"
    elif is_cancelled:
        gci_result = None
    elif be_gci is None or ss_gci is None or float(be_gci) == 0 or float(ss_gci) == 0:
        gci_result = None
    else:
        gci_result = "match" if round(float(be_gci), 2) == round(float(ss_gci), 2) else "mismatch"

    be_gci_val = float(be_gci) if be_gci is not None else None
    ss_gci_val = float(ss_gci) if ss_gci is not None else None

    be_close = row.get("be_close_date")
    ss_close = row.get("skyslope_close_date")
    close_result = None
    if saleguid is None:
        close_result = "no_skyslope_record"
    elif is_cancelled:
        close_result = None
    elif be_close is None or ss_close is None:
        close_result = None
    else:
        close_result = "match" if be_close == ss_close else "mismatch"

    be_close_str = be_close.isoformat() if hasattr(be_close, "isoformat") else be_close
    ss_close_str = ss_close.isoformat() if hasattr(ss_close, "isoformat") else ss_close

    status_result = "mismatch"
    if saleguid is None:
        status_result = "no_skyslope_record"
    elif is_cancelled:
        status_result = "match"
    elif be_status and be_status.lower() == "closed" and skyslope_status and skyslope_status.lower() in ["archived", "closed"]:
        status_result = "match"
    elif be_status and skyslope_status and be_status.lower() == skyslope_status.lower():
        status_result = "match"
    elif be_status and be_status.lower() == "pending" and skyslope_status and skyslope_status.lower() == "expired":
        status_result = None
    elif be_status is None or skyslope_status is None:
        status_result = "mismatch"
    else:
        status_result = "mismatch"

    be_sale = row.get("be_sale_price")
    ss_sale = row.get("skyslope_sale_price")
    sale_price_result = None
    if saleguid is None:
        sale_price_result = "no_skyslope_record"
    elif is_cancelled:
        sale_price_result = None
    elif be_sale is None or ss_sale is None:
        sale_price_result = None
    else:
        sale_price_result = "match" if float(be_sale) == float(ss_sale) else "mismatch"

    be_sale_val = float(be_sale) if be_sale is not None else None
    ss_sale_val = float(ss_sale) if ss_sale is not None else None

    if source_table == "otherincome_transactions":
        listing_price_result = None
        buyer_name_result = None
        seller_name_result = None
        buying_agent_result = None
        title_company_result = None

        be_list_val = None
        ss_list_val = None
        be_buyer = None
        ss_buyer = None
        be_seller = None
        ss_seller = None
        be_agent = None
        ss_agent = None
        be_title = None
        ss_title = None
    else:
        be_list = row.get("be_listing_price")
        ss_list = row.get("skyslope_listing_price")
        listing_price_result = None
        if saleguid is None:
            listing_price_result = "no_skyslope_record"
        elif is_cancelled:
            listing_price_result = None
        elif be_list is None or ss_list is None or float(be_list) == 0 or float(ss_list) == 0:
            listing_price_result = None
        else:
            listing_price_result = "match" if float(be_list) == float(ss_list) else "mismatch"

        be_list_val = float(be_list) if be_list is not None else None
        ss_list_val = float(ss_list) if ss_list is not None else None

        be_buyer = row.get("be_buyer_name")
        ss_buyer = row.get("skyslope_buyer_name")
        if saleguid is None:
            buyer_name_result = "no_skyslope_record"
        elif is_cancelled:
            buyer_name_result = None
        else:
            buyer_name_result = compare_names_fast(be_buyer, ss_buyer)

        be_seller = row.get("be_seller_name")
        ss_seller = row.get("skyslope_seller_name")
        if saleguid is None:
            seller_name_result = "no_skyslope_record"
        elif is_cancelled:
            seller_name_result = None
        else:
            seller_name_result = compare_names_fast(be_seller, ss_seller)

        be_agent = row.get("be_buying_agent_name")
        ss_agent = row.get("skyslope_buying_agent_name")
        if saleguid is None:
            buying_agent_result = "no_skyslope_record"
        elif is_cancelled:
            buying_agent_result = None
        else:
            buying_agent_result = compare_buying_agent_fast(be_agent, ss_agent)

        be_title = row.get("be_title_company")
        ss_title = row.get("skyslope_title_company")
        if saleguid is None:
            title_company_result = "no_skyslope_record"
        elif is_cancelled:
            title_company_result = None
        else:
            title_company_result = compare_names_fast(be_title, ss_title)

    return {
        "gross_commission": {
            "be_value": be_gci_val,
            "skyslope_value": ss_gci_val,
            "match_result": gci_result,
        },
        "close_date": {
            "be_value": be_close_str,
            "skyslope_value": ss_close_str,
            "match_result": close_result,
        },
        "status": {
            "be_value": be_status,
            "skyslope_value": skyslope_status,
            "match_result": status_result,
        },
        "sale_price": {
            "be_value": be_sale_val,
            "skyslope_value": ss_sale_val,
            "match_result": sale_price_result,
        },
        "listing_price": {
            "be_value": be_list_val,
            "skyslope_value": ss_list_val,
            "match_result": listing_price_result,
        },
        "buyer_name": {
            "be_value": be_buyer,
            "skyslope_value": ss_buyer,
            "match_result": buyer_name_result,
        },
        "seller_name": {
            "be_value": be_seller,
            "skyslope_value": ss_seller,
            "match_result": seller_name_result,
        },
        "buying_agent_name": {
            "be_value": be_agent,
            "skyslope_value": ss_agent,
            "match_result": buying_agent_result,
        },
        "title_company": {
            "be_value": be_title,
            "skyslope_value": ss_title,
            "match_result": title_company_result,
        },
    }


def get_no_skyslope_counts(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT
                (SELECT COUNT(*) FROM brokerage_engine WHERE skyslopefileid IS NULL) AS saleincome_no_skyslopefileid,
                (SELECT COUNT(*) FROM otherincome_transactions WHERE skyslopefileid IS NULL) AS otherincome_no_skyslopefileid
        """)
        return cur.fetchone()


def get_total_record_count(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT COUNT(*) AS total_record_count
            FROM (
                SELECT transaction_identifier_transactionid FROM brokerage_engine
                UNION ALL
                SELECT transaction_identifier_transactionid FROM otherincome_transactions
            ) combined_records
        """)
        row = cur.fetchone()
        return row["total_record_count"] if row else 0


@router.get("/reconciliation/summary")
def get_reconciliation_summary(conn=Depends(get_db)):
    total_record_count = get_total_record_count(conn)
    no_skyslope_counts = get_no_skyslope_counts(conn)

    return {
        "total_record_count": total_record_count,
        "saleincome_no_skyslopefileid": no_skyslope_counts["saleincome_no_skyslopefileid"],
        "otherincome_no_skyslopefileid": no_skyslope_counts["otherincome_no_skyslopefileid"],
    }


@router.get("/reconciliation/transactions")
def get_reconciliation_transactions(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1),
    search: Optional[str] = Query(default=None),
    mismatch_parameter: Optional[List[str]] = Query(default=None),
    source_table: Optional[List[str]] = Query(default=None),
    conn=Depends(get_db),
):
    parsed_mismatch_params = parse_mismatch_params(mismatch_parameter)
    parsed_source_tables = parse_source_table_params(source_table)
    where_clause, params = build_where_clause(search, parsed_mismatch_params, parsed_source_tables)

    database_pagination = not parsed_mismatch_params

    if database_pagination:
        data_query = f"""
            SELECT *, COUNT(*) OVER() AS _total_count
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
    else:
        query = f"""
            SELECT * FROM (
                {BASE_QUERY}
            ) cs
            {where_clause}
            ORDER BY cs.transactionid;
        """
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    results = []
    for row in rows:
        eval_result = evaluate_row(row)

        mismatch_params = [
            param
            for param, eval_data in eval_result.items()
            if eval_data["match_result"] == "mismatch"
        ]

        if not database_pagination and parsed_mismatch_params:
            if not any(p in mismatch_params for p in parsed_mismatch_params):
                continue

        source_table_label = SOURCE_TABLE_DISPLAY_NAMES.get(row["source_table"], row["source_table"])

        results.append({
            "transactionid": row["transactionid"],
            "saleguid": row["saleguid"],
            "propertyaddress": row["propertyaddress"],
            "source_table": source_table_label,
            "skyslope_stage": row.get("skyslope_stage"),
            "mismatched_parameters": mismatch_params,
            "review": {
                "review_status": row.get("review_status"),
                "notes": row.get("review_notes"),
                "updated_by": row.get("review_updated_by"),
            },
        })

    if database_pagination:
        paginated_data = results
    else:
        total_count = len(results)
        offset = (page - 1) * limit
        paginated_data = results[offset:offset + limit]

    return {
        "count": total_count,
        "pagination": {
            "page": page,
            "limit": limit,
            "total_pages": (total_count + limit - 1) // limit if limit else 1,
        },
        "filters": {
            "parameter": list(PARAMETER_DISPLAY_NAMES.values()),
            "source_table": ["sale income", "other income"],
        },
        "data": paginated_data,
    }


@router.get("/reconciliation/transaction/{transaction_id}")
def get_reconciliation_transaction_details(
    transaction_id: str,
    conn=Depends(get_db),
):
    query = f"""
        SELECT * FROM (
            {BASE_QUERY}
        ) cs
        WHERE cs.transactionid = %s;
    """

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query, (transaction_id,))
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    eval_result = evaluate_row(row)

    detailed_parameters = {
        param: {
            "be_value": details["be_value"],
            "skyslope_value": details["skyslope_value"],
            "match_result": details["match_result"],
        }
        for param, details in eval_result.items()
    }

    return {
        "transactionid": row["transactionid"],
        "saleguid": row["saleguid"],
        "propertyaddress": row["propertyaddress"],
        "source_table": row["source_table"],
        "be_status": row["be_status"],
        "skyslope_status": row["skyslope_status"],
        "parameters": detailed_parameters,
    }