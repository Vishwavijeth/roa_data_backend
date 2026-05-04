from fastapi import APIRouter
from db import get_conn
from psycopg2.extras import RealDictCursor

router = APIRouter()

@router.get("/transaction_specialist_listing")
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