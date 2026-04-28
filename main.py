from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from db import get_conn
from psycopg2.extras import RealDictCursor
from services.engine import run_field, run_brokerage_engine, get_skyslope_data, load_data

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/comparison/{field}")
def compare(field: str):
    return run_field(field)

@app.get("/brokerage_engine")
def brokerage_engine():
    return run_brokerage_engine()
def norm(x):
    return str(x or "").replace("\u00A0", "").strip().lower()

@app.get("/brokerage_engine/detail")
def brokerage_detail(transactionid: str):
    sales, be_data = load_data()

    txn = norm(transactionid)

    # find BE record
    be_record = next(
        (b for b in be_data
         if norm(b.get("transaction_identifier_transactionid")) == txn),
        None
    )

    if not be_record:
        raise HTTPException(status_code=404, detail="Transaction not found")

    skyslopefileid = be_record.get("skyslopefileid")

    # find sales record (FIXED KEY LOGIC)
    sale_record = next(
        (s for s in sales
         if norm(s.get("saleguid")) == norm(skyslopefileid)),
        None
    )
    match = True
    if sale_record:
        match = norm(skyslopefileid) == norm(sale_record.get("saleguid"))
    else:
        match = False

    return {
        "transactionid": transactionid,
        "brokerage_engine": {
            "property_address": be_record.get("property_address"),
            "sale_price": be_record.get("sale_price"),
            "listing_price": be_record.get("listing_price"),
            "office": be_record.get("listing_office"),
            "buyer": be_record.get("buyer_name"),
            "seller": be_record.get("seller_name"),
            "buying_agent_name": be_record.get("buying_agent_name"),
            "contract_date": be_record.get("contract_date"),
            "closed_date": be_record.get("closed_date"),
            "tags": be_record.get("tags"),
            "transaction_specialist": be_record.get("transaction_specialist"),
            "skyslopefileid": skyslopefileid
        },
        "skyslope": {
            "match": match,
            "saleguid": sale_record.get("saleguid") if sale_record else None,
            "property_address": sale_record.get("propertyaddress") if sale_record else None,
            "listingprice": sale_record.get("listingprice") if sale_record else None,
            "saleprice": sale_record.get("saleprice") if sale_record else None,
            "mlsnumber": sale_record.get("mlsnumber") if sale_record else None,
            "seller": sale_record.get("seller_full_name") if sale_record else None,
            "buyer": sale_record.get("buyer_full_name") if sale_record else None,
            "buying_agent": sale_record.get("agent_full_name") if sale_record else None,
            "buying_agent_email": sale_record.get("agent_mail_id") if sale_record else None,
            "reviewer_full_name": sale_record.get("reviewer_full_name") if sale_record else None,
            "status": sale_record.get("status") if sale_record else None,
            "contractacceptancedate": sale_record.get("contractacceptancedate") if sale_record else None,
            "escrowclosingdate": sale_record.get("escrowclosingdate") if sale_record else None,
            "canceldate": sale_record.get("canceldate") if sale_record else None
        }
    }

@app.get("/skyslope_api")
def skyslope_api():
    return get_skyslope_data()

from fastapi import HTTPException

@app.get("/skyslope/detail")
def skyslope_detail(saleguid: str):
    sales, be_data = load_data()

    sg = norm(saleguid)

    # 1. find SkySlope record
    sale_record = next(
        (s for s in sales if norm(s.get("saleguid")) == sg),
        None
    )

    if not sale_record:
        raise HTTPException(status_code=404, detail="Sale not found")

    # 2. extract skyslopefileid from BE side mapping logic
    be_record = next(
        (b for b in be_data if norm(b.get("skyslopefileid")) == sg),
        None
    )

    # 3. fallback: if BE is not directly mapped by saleguid,
    # try reverse mapping (important for your case)
    if not be_record:
        be_record = next(
            (
                b for b in be_data
                if norm(b.get("skyslopefileid")) == sg
            ),
            None
        )

    return {
        "saleguid": sg,

        # ---------------- SKY SLOPE ----------------
        "skyslope": {
            "saleguid": sale_record.get("saleguid"),
            "propertyaddress": sale_record.get("propertyaddress"),
            "listingprice": sale_record.get("listingprice"),
            "saleprice": sale_record.get("saleprice"),
            "mlsnumber": sale_record.get("mlsnumber"),
            "seller": sale_record.get("seller_full_name"),
            "buyer": sale_record.get("buyer_full_name"),
            "buyer_agent": sale_record.get("agent_full_name"),
            "buyer_agent_email": sale_record.get("agent_mail_id"),
            "reviewer": sale_record.get("reviewer_full_name"),
            "status": sale_record.get("status"),
            "contractacceptancedate": sale_record.get("contractacceptancedate"),
            "escrowclosingdate": sale_record.get("escrowclosingdate"),
            "canceldate": sale_record.get("canceldate")
        },

        # ---------------- BROKERAGE ENGINE ----------------
        "brokerage_engine": {
            "transactionid": be_record.get("transaction_identifier_transactionid") if be_record else None,
            "property_address": be_record.get("property_address") if be_record else None,
            "sale_price": be_record.get("sale_price") if be_record else None,
            "listing_price": be_record.get("listing_price") if be_record else None,
            "office": be_record.get("listing_office") if be_record else None,
            "buyer": be_record.get("buyer_name"),
            "seller": be_record.get("seller_name"),
            "buying_agent_name": be_record.get("buying_agent_name") if be_record else None,
            "contract_date": be_record.get("contract_date") if be_record else None,
            "closed_date": be_record.get("closed_date") if be_record else None,
            "tags": be_record.get("tags") if be_record else None,
            "transaction_specialist": be_record.get("transaction_specialist") if be_record else None,
            "skyslopefileid": be_record.get("skyslopefileid") if be_record else None
        }
    }

@app.get("/compare/sale_price")
def sale_price():
    conn = get_conn()

    try:
        query = """
WITH base AS (
    SELECT
        s.saleguid,
        be.transaction_identifier_transactionid AS transactionid,
        be.property_address AS propertyaddress,

        s.saleprice AS skyslope_sale_price,
        be.sale_price AS be_sale_price,
        be.tags AS tags,

        CASE 
            WHEN s.saleguid IS NULL THEN 'no_skyslope_record'
            WHEN s.saleprice IS DISTINCT FROM be.sale_price THEN 'mismatch'
            ELSE 'match'
        END AS match_result

    FROM brokerage_engine be
    LEFT JOIN sale s
        ON s.saleguid = be.skyslopefileid

    WHERE
        COALESCE(LOWER(be.tags), '') NOT LIKE '%complete%'
        AND COALESCE(LOWER(be.tags), '') NOT LIKE '%revoked%'
)

SELECT
    saleguid,
    transactionid,
    propertyaddress,
    skyslope_sale_price,
    be_sale_price,
    tags,
    match_result
FROM base
ORDER BY saleguid;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()

            cur.execute("""
                SELECT COUNT(*) AS mismatch_count
                FROM sale s
                JOIN brokerage_engine be
                    ON s.saleguid = be.skyslopefileid
                WHERE
                    LOWER(be.tags) NOT LIKE '%complete%'
                    AND LOWER(be.tags) NOT LIKE '%revoked%'
                    AND s.saleprice IS DISTINCT FROM be.sale_price
            """)
            mismatch_count = cur.fetchone()["mismatch_count"]

        return {
            "mismatch_count": mismatch_count,
            "data": rows
        }

    finally:
        conn.close()

@app.get("/compare/close_date")
def close_date():
    conn = get_conn()

    try:
        query = """
WITH base AS (
    SELECT
        s.saleguid,
        be.transaction_identifier_transactionid AS transactionid,
        be.property_address AS propertyaddress,

        s.escrowclosingdate AS skyslope_close_date,
        be.closed_date AS be_close_date,

        be.tags AS tags,

        CASE
            WHEN s.saleguid IS NULL THEN 'no_skyslope_record'
            WHEN s.escrowclosingdate IS DISTINCT FROM be.closed_date THEN 'mismatch'
            ELSE 'match'
        END AS match_result

    FROM brokerage_engine be
    LEFT JOIN sale s
        ON s.saleguid = be.skyslopefileid

    WHERE
        COALESCE(LOWER(be.tags), '') NOT LIKE '%complete%'
        AND COALESCE(LOWER(be.tags), '') NOT LIKE '%revoked%'
)

SELECT
    saleguid,
    transactionid,
    propertyaddress,
    skyslope_close_date,
    be_close_date,
    tags,
    match_result
FROM base
ORDER BY saleguid;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()

            cur.execute("""
                SELECT COUNT(*) AS mismatch_count
                FROM sale s
                JOIN brokerage_engine be
                    ON s.saleguid = be.skyslopefileid
                WHERE
                    LOWER(be.tags) NOT LIKE '%complete%'
                    AND LOWER(be.tags) NOT LIKE '%revoked%'
                    AND s.escrowclosingdate IS DISTINCT FROM be.closed_date
            """)
            mismatch_count = cur.fetchone()["mismatch_count"]

        return {
            "mismatch_count": mismatch_count,
            "data": rows
        }

    finally:
        conn.close()

@app.get("/compare/contract_date")
def contract_date():
    conn = get_conn()

    try:
        query = """
WITH base AS (
    SELECT
        s.saleguid,
        be.transaction_identifier_transactionid AS transactionid,
        be.property_address AS propertyaddress,

        s.contractacceptancedate AS skyslope_contract_date,
        be.contract_date AS be_contract_date,

        be.tags AS tags,

        CASE
            WHEN s.saleguid IS NULL THEN 'no_skyslope_record'
            WHEN s.contractacceptancedate IS DISTINCT FROM be.contract_date THEN 'mismatch'
            ELSE 'match'
        END AS match_result

    FROM brokerage_engine be
    LEFT JOIN sale s
        ON s.saleguid = be.skyslopefileid

    WHERE
        COALESCE(LOWER(be.tags), '') NOT LIKE '%complete%'
        AND COALESCE(LOWER(be.tags), '') NOT LIKE '%revoked%'
)

SELECT
    saleguid,
    transactionid,
    propertyaddress,
    skyslope_contract_date,
    be_contract_date,
    match_result
FROM base
ORDER BY saleguid;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()

            cur.execute("""
                SELECT COUNT(*) AS mismatch_count
                FROM sale s
                JOIN brokerage_engine be
                    ON s.saleguid = be.skyslopefileid
                WHERE
                    LOWER(be.tags) NOT LIKE '%complete%'
                    AND LOWER(be.tags) NOT LIKE '%revoked%'
                    AND s.contractacceptancedate IS DISTINCT FROM be.contract_date
            """)
            mismatch_count = cur.fetchone()["mismatch_count"]

        return {
            "mismatch_count": mismatch_count,
            "data": rows
        }

    finally:
        conn.close()

@app.get("/compare/listing_price")
def listing_price():
    conn = get_conn()

    try:
        query = """
WITH base AS (
    SELECT
        s.saleguid,
        be.transaction_identifier_transactionid AS transactionid,
        be.property_address AS propertyaddress,

        s.listingprice AS skyslope_listing_price,
        be.listing_price AS be_listing_price,

        be.tags AS tags,

        CASE
            WHEN s.saleguid IS NULL THEN 'no_skyslope_record'
            WHEN s.listingprice IS NULL
                 OR be.listing_price IS NULL
                 OR s.listingprice = 0
                 OR be.listing_price = 0
                THEN NULL
            WHEN s.listingprice IS DISTINCT FROM be.listing_price
                THEN 'mismatch'
            ELSE 'match'
        END AS match_result

    FROM brokerage_engine be
    LEFT JOIN sale s
        ON s.saleguid = be.skyslopefileid

    WHERE
        COALESCE(LOWER(be.tags), '') NOT LIKE '%complete%'
        AND COALESCE(LOWER(be.tags), '') NOT LIKE '%revoked%'
)

SELECT
    saleguid,
    transactionid,
    propertyaddress,
    skyslope_listing_price,
    be_listing_price,
    match_result
FROM base
ORDER BY saleguid;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()

            cur.execute("""
                SELECT COUNT(*) AS mismatch_count
                FROM sale s
                JOIN brokerage_engine be
                    ON s.saleguid = be.skyslopefileid
                WHERE
                    LOWER(be.tags) NOT LIKE '%complete%'
                    AND LOWER(be.tags) NOT LIKE '%revoked%'
                    AND s.listingprice IS NOT NULL
                    AND be.listing_price IS NOT NULL
                    AND s.listingprice <> 0
                    AND be.listing_price <> 0
                    AND s.listingprice IS DISTINCT FROM be.listing_price
            """)
            mismatch_count = cur.fetchone()["mismatch_count"]

        return {
            "mismatch_count": mismatch_count,
            "data": rows
        }

    finally:
        conn.close()

@app.get("/compare/gross_commission")
def compare_gross_commission():
    conn = get_conn()

    try:
        query = """
WITH base AS (
    SELECT
        s.saleguid,
        be.transaction_identifier_transactionid AS transactionid,
        be.property_address AS propertyaddress,

        scn.officeGrossCommissionOnSale AS skyslope_gross_commission,
        be.total_gross_commission AS be_gross_commission,

        CASE
            WHEN s.saleguid IS NULL THEN 'no_skyslope_record'
            WHEN scn.officeGrossCommissionOnSale IS NULL
                 OR be.total_gross_commission IS NULL
                 OR scn.officeGrossCommissionOnSale = 0
                 OR be.total_gross_commission = 0
                THEN NULL
            WHEN scn.officeGrossCommissionOnSale <> be.total_gross_commission
                THEN 'mismatch'
            ELSE 'match'
        END AS match_result

    FROM brokerage_engine be
    LEFT JOIN sale s
        ON s.saleguid = be.skyslopefileid

    LEFT JOIN sale_commission scn
        ON scn.saleguid = s.saleguid

    WHERE
        COALESCE(LOWER(be.tags), '') NOT LIKE '%complete%'
        AND COALESCE(LOWER(be.tags), '') NOT LIKE '%revoked%'
)

SELECT *
FROM base
ORDER BY saleguid;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()

            cur.execute("""
                SELECT COUNT(*) AS mismatch_count
                FROM sale s
                LEFT JOIN sale_commission scn
                    ON scn.saleguid = s.saleguid
                JOIN brokerage_engine be
                    ON s.saleguid = be.skyslopefileid
                WHERE
                    LOWER(be.tags) NOT LIKE '%complete%'
                    AND LOWER(be.tags) NOT LIKE '%revoked%'
                    AND scn.officeGrossCommissionOnSale IS NOT NULL
                    AND be.total_gross_commission IS NOT NULL
                    AND scn.officeGrossCommissionOnSale <> 0
                    AND be.total_gross_commission <> 0
                    AND scn.officeGrossCommissionOnSale <> be.total_gross_commission
            """)

            mismatch_count = cur.fetchone()["mismatch_count"]

        return {
            "mismatch_count": mismatch_count,
            "data": rows
        }

    finally:
        conn.close()

@app.get("/compare/transaction_reviewer_mapping")
def transaction_reviewer_mapping():
    conn = get_conn()

    try:
        query = """
SELECT
    s.saleguid,
    be.transaction_identifier_transactionid AS transactionid,
    be.property_address AS propertyaddress,

    COALESCE(r.firstname || ' ' || r.lastname, NULL) AS skyslope_reviewer_name,

    be.transaction_specialist AS be_transaction_specialist

FROM brokerage_engine be

LEFT JOIN sale s
    ON s.saleguid = be.skyslopefileid

LEFT JOIN users r
    ON s.reviewerguid = r.userguid

WHERE
    COALESCE(LOWER(be.tags), '') NOT LIKE '%complete%'
    AND COALESCE(LOWER(be.tags), '') NOT LIKE '%revoked%'

ORDER BY s.saleguid;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()

        return {
            "data": rows
        }

    finally:
        conn.close()

@app.get("/transaction_specialist_listing")
def transaction_specialist_listing():
    conn = get_conn()

    try:
        query = """
        SELECT
            be.transaction_identifier_transactionid AS transactionid,
            be.property_address AS propertyaddress,

            be.sale_price AS be_sale_price,
            be.listing_price AS listing_price,
            be.closed_date AS be_closed_date,

            -- workflow status (ONLY complete, revoked, pending)
            TRIM(
                CONCAT_WS(', ',

                    CASE
                        WHEN LOWER(COALESCE(be.tags, '')) LIKE '%complete%'
                        THEN 'Complete'
                    END,

                    CASE
                        WHEN LOWER(COALESCE(be.tags, '')) LIKE '%revoked%'
                        THEN 'Revoked'
                    END,

                    CASE
                        WHEN NOT (
                            LOWER(COALESCE(be.tags, '')) LIKE '%complete%' OR
                            LOWER(COALESCE(be.tags, '')) LIKE '%revoked%'
                        )
                        THEN 'Pending'
                    END

                )
            ) AS be_workflow_status,

            be.transaction_specialist AS transaction_specialist,
            be.skyslopefileid AS skyslopefileid

        FROM brokerage_engine be

        ORDER BY be.transaction_identifier_transactionid;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()

        return {
            "count": len(rows),
            "data": rows
        }

    finally:
        conn.close()

@app.get("/reviewer_listing")
def reviewer_listing():
    conn = get_conn()

    try:
        query = """
        SELECT
            s.saleguid AS saleguid,

            -- build property address
            CONCAT_WS(', ',
                CONCAT_WS(' ', sp.streetnumber, sp.streetaddress),
                sp.city,
                sp.state,
                sp.zip
            ) AS propertyaddress,

            s.saleprice AS sale_price,
            s.listingprice AS listing_price,
            s.escrowclosingdate AS escrow_close_date,

            s.status AS ss_status,

            NULL AS be_workflow_status,

            -- reviewer name
            COALESCE(r.firstname || ' ' || r.lastname, '') AS reviewer_name

        FROM sale s

        LEFT JOIN sale_property sp
            ON s.saleguid = sp.saleguid

        LEFT JOIN users r
            ON s.reviewerguid = r.userguid

        ORDER BY s.saleguid;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()

        return {
            "count": len(rows),
            "data": rows
        }

    finally:
        conn.close()

@app.get("/transaction_specialist_dashboard")
def transaction_specialist_dashboard(
    from_date: str = Query(None),
    to_date: str = Query(None),
    state: str = Query(None)
):
    conn = get_conn()

    try:
        query = """
        SELECT
            COALESCE(be.transaction_specialist, 'Unassigned') AS transaction_specialist,

            COUNT(*) FILTER (
                WHERE NOT (
                    LOWER(COALESCE(be.tags, '')) LIKE '%%complete%%' OR
                    LOWER(COALESCE(be.tags, '')) LIKE '%%revoked%%'
                )
            ) AS transactions_outstanding,

            COUNT(*) FILTER (
                WHERE
                    LOWER(COALESCE(be.tags, '')) LIKE '%%complete%%' OR
                    LOWER(COALESCE(be.tags, '')) LIKE '%%revoked%%'
            ) AS transactions_closed

        FROM brokerage_engine be
        WHERE 1=1
        """

        params = []

        # date filter
        if from_date:
            query += " AND be.closed_date::date >= %s"
            params.append(from_date)

        if to_date:
            query += " AND be.closed_date::date <= %s"
            params.append(to_date)

        # state filter
        if state:
            query += " AND LOWER(COALESCE(be.state, '')) = LOWER(%s)"
            params.append(state)

        query += """
        GROUP BY COALESCE(be.transaction_specialist, 'Unassigned')
        ORDER BY transaction_specialist;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()

        return {
            "count": len(rows),
            "data": rows
        }

    finally:
        conn.close()

from psycopg2.extras import RealDictCursor

@app.get("/transaction_specialist/state")
def get_states():
    conn = get_conn()

    try:
        query = """
        SELECT DISTINCT state
        FROM brokerage_engine
        WHERE state IS NOT NULL AND TRIM(state) <> ''
        ORDER BY state;
        """

        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()

        states = [r[0] for r in rows]

        return {
            "count": len(states),
            "data": states
        }

    finally:
        conn.close()

@app.get("/reviewer_dashboard")
def reviewer_dashboard(
    from_date: str = Query(None),
    to_date: str = Query(None),
    state: str = Query(None)
):
    conn = get_conn()

    try:
        query = """
        SELECT
            COALESCE(r.firstname || ' ' || r.lastname, 'Unassigned') AS reviewer_full_name,

            COUNT(*) FILTER (
                WHERE LOWER(COALESCE(s.status, '')) = 'pending'
            ) AS transactions_outstanding,

            COUNT(*) FILTER (
                WHERE LOWER(COALESCE(s.status, '')) = 'closed'
            ) AS transactions_closed

        FROM sale s
        LEFT JOIN users r
            ON s.reviewerguid = r.userguid
        LEFT JOIN sale_property sp
            ON s.saleguid = sp.saleguid

        WHERE 1=1
        """

        params = []

        if from_date:
            query += " AND s.escrowclosingdate >= %s"
            params.append(from_date)

        if to_date:
            query += " AND s.escrowclosingdate <= %s"
            params.append(to_date)

        # ✅ CORRECT state filter
        if state:
            query += " AND LOWER(sp.state) = LOWER(%s)"
            params.append(state)

        query += """
        GROUP BY reviewer_full_name
        ORDER BY reviewer_full_name;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()

        return {
            "count": len(rows),
            "data": rows
        }

    finally:
        conn.close()

@app.get("/reviewer_dashboard/state")
def get_states():
    conn = get_conn()

    try:
        query = """
        SELECT DISTINCT state
        FROM sale_property
        WHERE state IS NOT NULL AND TRIM(state) <> ''
        ORDER BY state;
        """

        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()

        return {
            "data": [row[0] for row in rows]
        }

    finally:
        conn.close()