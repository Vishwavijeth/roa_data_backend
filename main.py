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
                    WHEN s.saleprice IS DISTINCT FROM be.sale_price
                    THEN 'mismatch'
                    ELSE 'match'
                END AS match_result
            FROM sale s
            JOIN brokerage_engine be
                ON s.saleguid = be.skyslopefileid
            WHERE
                LOWER(be.tags) NOT LIKE '%complete%'
                AND LOWER(be.tags) NOT LIKE '%revoked%'
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
                    WHEN s.escrowclosingdate IS DISTINCT FROM be.closed_date
                    THEN 'mismatch'
                    ELSE 'match'
                END AS match_result
            FROM sale s
            JOIN brokerage_engine be
                ON s.saleguid = be.skyslopefileid
            WHERE
                LOWER(be.tags) NOT LIKE '%complete%'
                AND LOWER(be.tags) NOT LIKE '%revoked%'
        )
        SELECT
            saleguid

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

@app.get("/compare/transaction_specialist_dashboard")
def transaction_specialist_dashboard():
    conn = get_conn()

    try:
        query = """
        SELECT
            be.transaction_identifier_transactionid AS transactionid,
            be.property_address AS propertyaddress,

            be.sale_price AS be_sale_price,
            be.listing_price AS listing_price,
            be.closed_date AS be_closed_date,

            s.status AS ss_status,

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
            s.saleguid AS saleguid

        FROM sale s
        JOIN brokerage_engine be
            ON s.saleguid = be.skyslopefileid

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

@app.get("/compare/reviewer_dashboard")
def reviewer_dashboard():
    conn = get_conn()

    try:
        query = """
        SELECT
            s.saleguid AS transactionid,
            be.property_address AS propertyaddress,

            s.saleprice AS sale_price,
            s.listingprice AS listing_price,
            s.escrowclosingdate AS escrow_close_date,

            s.status AS ss_status,

            -- workflow status (complete / revoked / pending)
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

            -- reviewer name (CORRECT SOURCE)
            COALESCE(r.firstname || ' ' || r.lastname, '') AS reviewer_name

        FROM sale s

        LEFT JOIN users r
            ON s.reviewerguid = r.userguid

        JOIN brokerage_engine be
            ON s.saleguid = be.skyslopefileid

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