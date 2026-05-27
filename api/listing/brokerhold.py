from fastapi import APIRouter, Query
from db import get_conn
from psycopg2.extras import RealDictCursor

router = APIRouter()

@router.get("/broker-hold/listing")
def broker_hold_listing(
    buying_agent_name: list[str] = Query(default=None),
    page: int = Query(default=1, ge=1)
):
    conn = get_conn()
    page_size = 50
    offset = (page - 1) * page_size

    try:
        agent_filter = ""
        params = {
            "page_size": page_size,
            "offset": offset
        }

        if buying_agent_name:
            agent_conditions = []
            for idx, agent in enumerate(buying_agent_name):
                key = f"buying_agent_name_{idx}"
                agent_conditions.append(
                    f"be.buying_agent_name ILIKE '%%' || %({key})s || '%%'"
                )
                params[key] = agent

            agent_filter = f"""
                AND (
                    {" OR ".join(agent_conditions)}
                )
            """

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

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

            total_count = rows[0]["total_count"] if rows else 0

            data = [
                {k: v for k, v in row.items() if k != "total_count"}
                for row in rows
            ]

            cur.execute("""
                SELECT COUNT(*) AS broker_hold_count
                FROM brokerage_engine
                WHERE tags ILIKE '%%brokerhold%%'
            """)
            broker_hold_count = cur.fetchone()["broker_hold_count"]

            # get distinct buying agent names for multi-select dropdown
            cur.execute("""
                SELECT DISTINCT TRIM(agent_name) AS buying_agent_name
                FROM brokerage_engine,
                LATERAL UNNEST(STRING_TO_ARRAY(buying_agent_name, ',')) AS agent_name
                WHERE tags ILIKE '%%brokerhold%%'
                  AND buying_agent_name IS NOT NULL
                  AND TRIM(agent_name) <> ''
                ORDER BY buying_agent_name
            """)

            buying_agent_names = [
                row["buying_agent_name"]
                for row in cur.fetchall()
            ]

        return {
            "broker_hold_count": broker_hold_count,
            "filters": {
                "buying_agent_names": buying_agent_names
            },
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_count": total_count,
                "total_pages": -(-total_count // page_size)
            },
            "data": data
        }

    finally:
        conn.close()