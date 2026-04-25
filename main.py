from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from db import get_conn
from psycopg2.extras import RealDictCursor
from services.engine import run_field, run_brokerage_engine, get_skyslope_data

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

@app.get("/skyslope_api")
def skyslope_api():
    return get_skyslope_data()


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

            NULL AS ss_status,  -- no skyslope join anymore

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

            NULL AS saleguid  -- since no join

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
            s.saleguid AS transactionid,
            NULL AS propertyaddress,

            s.saleprice AS sale_price,
            s.listingprice AS listing_price,
            s.escrowclosingdate AS escrow_close_date,

            s.status AS ss_status,

            NULL AS be_workflow_status,

            -- reviewer name
            COALESCE(r.firstname || ' ' || r.lastname, '') AS reviewer_name

        FROM sale s

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
def transaction_specialist_dashboard():
    conn = get_conn()

    try:
        query = """
        SELECT
            COALESCE(be.transaction_specialist, 'Unassigned') AS transaction_specialist,

            -- Outstanding = Pending
            COUNT(*) FILTER (
                WHERE NOT (
                    LOWER(COALESCE(be.tags, '')) LIKE '%complete%' OR
                    LOWER(COALESCE(be.tags, '')) LIKE '%revoked%'
                )
            ) AS transactions_outstanding,

            -- Closed = Complete OR Revoked
            COUNT(*) FILTER (
                WHERE 
                    LOWER(COALESCE(be.tags, '')) LIKE '%complete%' OR
                    LOWER(COALESCE(be.tags, '')) LIKE '%revoked%'
            ) AS transactions_closed,

            MAX(be.closed_date) FILTER (
                WHERE 
                    LOWER(COALESCE(be.tags, '')) LIKE '%complete%' OR
                    LOWER(COALESCE(be.tags, '')) LIKE '%revoked%'
            ) AS latest_closed_date

        FROM brokerage_engine be

        GROUP BY COALESCE(be.transaction_specialist, 'Unassigned')
        ORDER BY transaction_specialist;
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

@app.get("/reviewer_dashboard")
def reviewer_dashboard():
    conn = get_conn()

    try:
        query = """
        SELECT
            COALESCE(r.firstname || ' ' || r.lastname, 'Unassigned') AS reviewer_full_name,

            -- outstanding = pending
            COUNT(*) FILTER (
                WHERE LOWER(COALESCE(s.status, '')) = 'pending'
            ) AS transactions_outstanding,

            -- closed
            COUNT(*) FILTER (
                WHERE LOWER(COALESCE(s.status, '')) = 'closed'
            ) AS transactions_closed

        FROM sale s

        LEFT JOIN users r
            ON s.reviewerguid = r.userguid

        GROUP BY reviewer_full_name
        ORDER BY reviewer_full_name;
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