from fastapi import APIRouter
from psycopg2.extras import RealDictCursor
from services.comparison import compare_names, compare_buying_agent
from db import get_conn

router = APIRouter()

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

                    be.transaction_status,
                    s.status,

                    s.contractacceptancedate AS skyslope_contract_date,
                    be.contract_date AS be_contract_date,

                    CASE
                        WHEN LOWER(be.transaction_status) = 'cancelled'
                             AND LOWER(s.status) IN ('canceled/app', 'canceled/pend')
                        THEN NULL

                        WHEN be.transaction_status ILIKE 'cancelled'
                            AND (
                                s.status ILIKE 'canceled/pend'
                                OR s.status ILIKE 'canceled/app'
                            )
                            THEN NULL

                        WHEN s.saleguid IS NULL
                        THEN 'no_skyslope_record'

                        WHEN s.contractacceptancedate IS DISTINCT FROM be.contract_date
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
                WHERE LOWER(be.transaction_status) <> 'cancelled'
                  AND s.contractacceptancedate IS DISTINCT FROM be.contract_date
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

@router.get("/compare/buyer_name")
def compare_buyer_name():
    conn = get_conn()

    try:
        query = """
            SELECT
                s.saleguid,
                be.transaction_identifier_transactionid AS transactionid,
                be.property_address AS propertyaddress,

                be.transaction_status,
                s.status,

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
                ) AS skyslope_buyer_name,

                be.buyer_name AS be_buyer_name

            FROM brokerage_engine be
            LEFT JOIN sale s
                ON s.saleguid = be.skyslopefileid

            ORDER BY s.saleguid;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()

        results = []
        mismatch_count = 0

        for row in rows:
            skyslope_buyer_name = row.get("skyslope_buyer_name")
            be_buyer_name = row.get("be_buyer_name")

            be_status = row.get("transaction_status")
            s_status = row.get("status")

            if row.get("saleguid") is None:
                match_result = "no_skyslope_record"

            elif (
                    be_status
                    and be_status.lower() == "cancelled"
                    and s_status
                    and s_status.lower() in ["canceled/pend", "canceled/app"]
                ):
                match_result = None

            else:
                match_result = compare_names(
                    be_buyer_name,
                    skyslope_buyer_name
                )

            if match_result == "mismatch":
                mismatch_count += 1

            results.append({
                "saleguid": row.get("saleguid"),
                "transactionid": row.get("transactionid"),
                "propertyaddress": row.get("propertyaddress"),
                "transaction_status": be_status,
                "status": s_status,
                "skyslope_buyer_name": skyslope_buyer_name,
                "be_buyer_name": be_buyer_name,
                "match_result": match_result
            })

        return {
            "mismatch_count": mismatch_count,
            "data": results
        }

    finally:
        conn.close()

@router.get("/compare/seller_name")
def compare_seller_name():
    conn = get_conn()

    try:
        query = """
            SELECT
                s.saleguid,
                be.transaction_identifier_transactionid AS transactionid,
                be.property_address AS propertyaddress,

                be.transaction_status,
                s.status,

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
                ) AS skyslope_seller_name,

                be.seller_name AS be_seller_name

            FROM brokerage_engine be
            LEFT JOIN sale s
                ON s.saleguid = be.skyslopefileid

            ORDER BY s.saleguid;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()

        results = []
        mismatch_count = 0

        for row in rows:
            skyslope_seller_name = row.get("skyslope_seller_name")
            be_seller_name = row.get("be_seller_name")

            be_status = row.get("transaction_status")
            s_status = row.get("status")

            if row.get("saleguid") is None:
                match_result = "no_skyslope_record"

            elif (
                    be_status
                    and be_status.lower() == "cancelled"
                    and s_status
                    and s_status.lower() in ["canceled/pend", "canceled/app"]
                ):
                match_result = None

            else:
                match_result = compare_names(
                    be_seller_name,
                    skyslope_seller_name
                )

            if match_result == "mismatch":
                mismatch_count += 1

            results.append({
                "saleguid": row.get("saleguid"),
                "transactionid": row.get("transactionid"),
                "propertyaddress": row.get("propertyaddress"),
                "transaction_status": be_status,
                "status": row.get("status"),
                "skyslope_seller_name": skyslope_seller_name,
                "be_seller_name": be_seller_name,
                "match_result": match_result
            })

        return {
            "mismatch_count": mismatch_count,
            "data": results
        }

    finally:
        conn.close()

@router.get("/compare/buying_agent_name")
def compare_buying_agent_name():
    conn = get_conn()

    try:
        query = """
            SELECT
                s.saleguid,
                be.transaction_identifier_transactionid AS transactionid,
                be.property_address AS propertyaddress,

                be.transaction_status,
                s.status,

                COALESCE(
                    (
                        SELECT TRIM(COALESCE(uu.firstname, '') || ' ' || COALESCE(uu.lastname, ''))
                        FROM users uu
                        WHERE uu.userguid = s.agentguid
                    ),
                    ''
                ) AS skyslope_buying_agent_name,

                be.buying_agent_name AS be_buying_agent_name

            FROM brokerage_engine be
            LEFT JOIN sale s
                ON s.saleguid = be.skyslopefileid

            ORDER BY s.saleguid;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()

        results = []
        mismatch_count = 0

        for row in rows:
            skyslope_buying_agent_name = row.get("skyslope_buying_agent_name")
            be_buying_agent_name = row.get("be_buying_agent_name")

            be_status = row.get("transaction_status")
            s_status = row.get("status")

            if row.get("saleguid") is None:
                match_result = "no_skyslope_record"

            elif (
                    be_status
                    and be_status.lower() == "cancelled"
                    and s_status
                    and s_status.lower() in ["canceled/pend", "canceled/app"]
                ):
                match_result = None

            else:
                match_result = compare_buying_agent(
                    be_buying_agent_name,
                    skyslope_buying_agent_name
                )

            if match_result == "mismatch":
                mismatch_count += 1

            results.append({
                "saleguid": row.get("saleguid"),
                "transactionid": row.get("transactionid"),
                "propertyaddress": row.get("propertyaddress"),
                "transaction_status": be_status,
                "status": row.get("status"),
                "skyslope_buying_agent_name": skyslope_buying_agent_name,
                "be_buying_agent_name": be_buying_agent_name,
                "match_result": match_result
            })

        return {
            "mismatch_count": mismatch_count,
            "data": results
        }

    finally:
        conn.close()

@router.get("/compare/title_company")
def compare_title_company():
    conn = get_conn()

    try:
        query = """
            SELECT
                s.saleguid,
                be.transaction_identifier_transactionid AS transactionid,
                be.property_address AS propertyaddress,

                be.transaction_status,
                s.status,

                COALESCE(
                    (
                        SELECT sc.company
                        FROM sale_contact sc
                        WHERE sc.saleguid = s.saleguid
                        AND LOWER(sc.role) = 'titlecompany'
                        LIMIT 1
                    ),
                    ''
                ) AS skyslope_title_company,

                be.da_title_company AS be_title_company

            FROM brokerage_engine be
            LEFT JOIN sale s
                ON s.saleguid = be.skyslopefileid

            ORDER BY s.saleguid;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()

        results = []
        mismatch_count = 0

        for row in rows:
            skyslope_title_company = row.get("skyslope_title_company")
            be_title_company = row.get("be_title_company")

            be_status = row.get("transaction_status")
            s_status = row.get("status")

            if row.get("saleguid") is None:
                match_result = "no_skyslope_record"

            elif (
                    be_status
                    and be_status.lower() == "cancelled"
                    and s_status
                    and s_status.lower() in ["canceled/pend", "canceled/app"]
                ):
                match_result = None

            else:
                match_result = compare_names(
                    be_title_company,
                    skyslope_title_company
                )

            if match_result == "mismatch":
                mismatch_count += 1

            results.append({
                "saleguid": row.get("saleguid"),
                "transactionid": row.get("transactionid"),
                "propertyaddress": row.get("propertyaddress"),
                "transaction_status": be_status,
                "status": row.get("status"),
                "skyslope_title_company": skyslope_title_company,
                "be_title_company": be_title_company,
                "match_result": match_result
            })

        return {
            "mismatch_count": mismatch_count,
            "data": results
        }

    finally:
        conn.close()