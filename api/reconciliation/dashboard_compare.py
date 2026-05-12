from fastapi import APIRouter
from psycopg2.extras import RealDictCursor
from db import get_conn

router = APIRouter()

@router.get("/compare/sale_price")
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

                    CASE 
                        WHEN s.saleguid IS NULL THEN 'no_skyslope_record'
                        WHEN s.saleprice IS DISTINCT FROM be.sale_price THEN 'mismatch'
                        ELSE 'match'
                    END AS match_result

                FROM brokerage_engine be
                LEFT JOIN sale s
                    ON s.saleguid = be.skyslopefileid
            )

            SELECT
                saleguid,
                transactionid,
                propertyaddress,
                skyslope_sale_price,
                be_sale_price,
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
                WHERE s.saleprice IS DISTINCT FROM be.sale_price
            """)
            mismatch_count = cur.fetchone()["mismatch_count"]

        return {
            "mismatch_count": mismatch_count,
            "data": rows
        }

    finally:
        conn.close()

@router.get("/compare/close_date")
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

                    CASE
                        WHEN s.saleguid IS NULL THEN 'no_skyslope_record'
                        WHEN s.escrowclosingdate IS DISTINCT FROM be.closed_date THEN 'mismatch'
                        ELSE 'match'
                    END AS match_result

                FROM brokerage_engine be
                LEFT JOIN sale s
                    ON s.saleguid = be.skyslopefileid
            )

            SELECT
                saleguid,
                transactionid,
                propertyaddress,
                skyslope_close_date,
                be_close_date,
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
                WHERE s.escrowclosingdate IS DISTINCT FROM be.closed_date
            """)
            mismatch_count = cur.fetchone()["mismatch_count"]

        return {
            "mismatch_count": mismatch_count,
            "data": rows
        }

    finally:
        conn.close()

@router.get("/compare/contract_date")
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

                    CASE
                        WHEN s.saleguid IS NULL THEN 'no_skyslope_record'
                        WHEN s.contractacceptancedate IS DISTINCT FROM be.contract_date THEN 'mismatch'
                        ELSE 'match'
                    END AS match_result

                FROM brokerage_engine be
                LEFT JOIN sale s
                    ON s.saleguid = be.skyslopefileid
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
                WHERE s.contractacceptancedate IS DISTINCT FROM be.contract_date
            """)
            mismatch_count = cur.fetchone()["mismatch_count"]

        return {
            "mismatch_count": mismatch_count,
            "data": rows
        }

    finally:
        conn.close()

@router.get("/compare/listing_price")
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
                    s.listingprice IS NOT NULL
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

@router.get("/compare/gross_commission")
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
                    scn.officeGrossCommissionOnSale IS NOT NULL
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

@router.get("/compare/transaction_reviewer_mapping")
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

@router.get("/compare/status")
def status():
    conn = get_conn()

    try:
        query = """
            WITH base AS (
                SELECT
                    s.saleguid,
                    be.transaction_identifier_transactionid AS transactionid,
                    be.property_address AS propertyaddress,
                    be.transaction_status AS be_status,
                    s.status AS skyslope_status,

                    CASE
                        -- both null handling (optional safety)
                        WHEN be.transaction_status IS NULL AND s.status IS NULL THEN 'match'

                        -- normalize cancelled variations
                        WHEN LOWER(be.transaction_status) IN ('cancelled', 'canceled')
                             AND LOWER(s.status) IN ('canceled', 'cancelled', 'canceled/pend', 'canceled/app')
                        THEN 'match'

                        -- direct match
                        WHEN LOWER(be.transaction_status) = LOWER(s.status)
                        THEN 'match'

                        ELSE 'mismatch'
                    END AS match_result

                FROM brokerage_engine be
                LEFT JOIN sale s
                    ON s.saleguid = be.skyslopefileid
            )

            SELECT
                saleguid,
                transactionid,
                propertyaddress,
                be_status,
                skyslope_status,
                match_result
            FROM base
            ORDER BY saleguid;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()

            cur.execute("""
                SELECT COUNT(*) AS mismatch_count
                FROM brokerage_engine be
                LEFT JOIN sale s
                    ON s.saleguid = be.skyslopefileid
                WHERE NOT (
                    (LOWER(be.transaction_status) IN ('cancelled', 'canceled')
                     AND LOWER(s.status) IN ('canceled', 'cancelled', 'canceled/pend', 'canceled/app'))
                    OR LOWER(be.transaction_status) = LOWER(s.status)
                )
            """)
            mismatch_count = cur.fetchone()["mismatch_count"]

        return {
            "mismatch_count": mismatch_count,
            "data": rows
        }

    finally:
        conn.close()