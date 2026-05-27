from fastapi import APIRouter, Query
from db import get_conn
from psycopg2.extras import RealDictCursor

router = APIRouter()

@router.get("/broker-hold/listing")
def broker_hold_listing(
    buying_agent_name: str = Query(default=None),
    page: int = Query(default=1, ge=1)
):
    conn = get_conn()
    page_size = 50
    offset = (page - 1) * page_size

    try:
        agent_filter = """
            AND (
                be.buying_agent_name ILIKE '%%' || %(buying_agent_name)s || '%%'
            )
        """ if buying_agent_name else ""

        query = f"""
            WITH broker_hold_agents AS (
                SELECT DISTINCT TRIM(agent_name) AS buying_agent_name
                FROM brokerage_engine,
                LATERAL UNNEST(STRING_TO_ARRAY(buying_agent_name, ',')) AS agent_name
                WHERE tags ILIKE '%%brokerhold%%'
                  AND buying_agent_name IS NOT NULL
            ),
            broker_hold_records AS (
                SELECT
                    be.transaction_identifier_transactionid AS transactionid,
                    be.property_address AS propertyaddress,
                    be.buying_agent_name,
                    be.transaction_status,
                    be.closed_date,
                    be.sale_price,
                    be.tags
                FROM brokerage_engine be
                WHERE EXISTS (
                    SELECT 1
                    FROM broker_hold_agents bha
                    WHERE be.buying_agent_name ILIKE '%%' || bha.buying_agent_name || '%%'
                )
                {agent_filter}

                UNION

                SELECT
                    be.transaction_identifier_transactionid AS transactionid,
                    be.property_address AS propertyaddress,
                    be.buying_agent_name,
                    be.transaction_status,
                    be.closed_date,
                    be.sale_price,
                    be.tags
                FROM brokerage_engine be
                WHERE be.buying_agent_name IS NULL
                  AND be.tags ILIKE '%%brokerhold%%'
                  {'AND FALSE' if buying_agent_name else ''}
            ),
            paginated AS (
                SELECT *, COUNT(*) OVER() AS total_count
                FROM broker_hold_records
                ORDER BY buying_agent_name, closed_date DESC
                LIMIT %(page_size)s OFFSET %(offset)s
            )
            SELECT * FROM paginated
        """

        params = {
            "page_size": page_size,
            "offset": offset,
            **({"buying_agent_name": buying_agent_name} if buying_agent_name else {})
        }

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

            total_count = rows[0]["total_count"] if rows else 0
            # remove total_count from each row
            data = [{k: v for k, v in row.items() if k != "total_count"} for row in rows]

            cur.execute("""
                SELECT COUNT(*) AS broker_hold_count
                FROM brokerage_engine
                WHERE tags ILIKE '%%brokerhold%%'
            """)
            broker_hold_count = cur.fetchone()["broker_hold_count"]

        return {
            "broker_hold_count": broker_hold_count,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_count": total_count,
                "total_pages": -(-total_count // page_size)  # ceiling division
            },
            "data": data
        }

    finally:
        conn.close()


@router.get("/broker-hold/agents")
def broker_hold_agents():
    conn = get_conn()

    try:
        query = """
            SELECT DISTINCT TRIM(agent_name) AS buying_agent_name
            FROM brokerage_engine,
            LATERAL UNNEST(STRING_TO_ARRAY(buying_agent_name, ',')) AS agent_name
            WHERE tags ILIKE '%%brokerhold%%'
              AND buying_agent_name IS NOT NULL
              AND TRIM(agent_name) <> ''
            ORDER BY buying_agent_name
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()

        return {
            "data": [row["buying_agent_name"] for row in rows]
        }

    finally:
        conn.close()