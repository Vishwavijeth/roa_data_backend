from enum import Enum

from fastapi import APIRouter, Depends, Query
from psycopg2.extras import RealDictCursor

from db import get_db
from services.quickbooks import fetch_ar_balance

router = APIRouter(tags=["pre-cda"])


class PreCDAFilter(str, Enum):
    all = "all"
    transaction_flag = "transaction_flag"
    agent_flag = "agent_flag"
    all_flagged = "all_flagged"


async def fetch_pre_cda_data(
    filter: str,
    page: int = 1,
    search: str = None,
    agent_name: str = None,
    conn=None
):
    limit = 50
    offset = (page - 1) * limit
    params = []

    common_cte = """
        WITH brokerhold_agents AS (
            SELECT DISTINCT LOWER(TRIM(agent_name)) AS brokerhold_agent_name
            FROM (
                SELECT regexp_split_to_table(be.buying_agent_name, '\\s*,\\s*') AS agent_name
                FROM brokerage_engine be
                WHERE be.tags ILIKE '%%brokerhold%%'
                  AND be.buying_agent_name IS NOT NULL
                  AND TRIM(be.buying_agent_name) <> ''

                UNION ALL

                SELECT regexp_split_to_table(oit.agents, '\\s*,\\s*') AS agent_name
                FROM otherincome_transactions oit
                WHERE oit.tags ILIKE '%%brokerhold%%'
                  AND oit.agents IS NOT NULL
                  AND TRIM(oit.agents) <> ''
            ) x
            WHERE agent_name IS NOT NULL
              AND TRIM(agent_name) <> ''
        ),
        brokerage_base AS (
            SELECT
                'brokerage_engine'::text AS source_table,
                be.transaction_identifier_transactionid AS transaction_id,
                be.skyslopefileid::text AS skyslopefileid,
                be.property_address,
                be.tags,
                be.buying_agent_name AS agent_name,
                be.buying_agent_email AS email,
                be.sale_price::numeric AS be_amount,
                be.closed_date::date AS be_closed_date,
                be.transaction_status AS be_transaction_status,
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
            WHERE be.buying_agent_name IS NOT NULL
              AND TRIM(be.buying_agent_name) <> ''
              AND EXISTS (
                  SELECT 1
                  FROM regexp_split_to_table(be.buying_agent_name, '\\s*,\\s*') AS split_agent(agent_name)
                  JOIN brokerhold_agents bha
                    ON bha.brokerhold_agent_name = LOWER(TRIM(split_agent.agent_name))
                  WHERE TRIM(split_agent.agent_name) <> ''
              )
        ),
        other_income_base AS (
            SELECT
                'otherincome_transactions'::text AS source_table,
                oit.transaction_identifier_transactionid AS transaction_id,
                oit.skyslopefileid::text AS skyslopefileid,
                oit.property_address,
                oit.tags,
                oit.agents AS agent_name,
                NULL::text AS email,
                oit.income_received::numeric AS be_amount,
                oit.income_received_date::date AS be_closed_date,
                oit.transaction_status AS be_transaction_status,
                oit.gross_commission::numeric AS be_gross_commission
            FROM otherincome_transactions oit
            WHERE oit.agents IS NOT NULL
              AND TRIM(oit.agents) <> ''
              AND EXISTS (
                  SELECT 1
                  FROM regexp_split_to_table(oit.agents, '\\s*,\\s*') AS split_agent(agent_name)
                  JOIN brokerhold_agents bha
                    ON bha.brokerhold_agent_name = LOWER(TRIM(split_agent.agent_name))
                  WHERE TRIM(split_agent.agent_name) <> ''
              )
        ),
        combined_source AS (
            SELECT * FROM brokerage_base
            UNION ALL
            SELECT * FROM other_income_base
        ),
        base AS (
            SELECT
                cs.source_table,
                cs.transaction_id,
                cs.skyslopefileid,
                cs.property_address,
                cs.tags,
                cs.agent_name,
                cs.email,
                cs.be_amount,
                s.saleprice::numeric AS ss_sale_price,
                cs.be_closed_date,
                s.escrowclosingdate::date AS ss_closed_date,
                cs.be_transaction_status,
                s.status AS ss_transaction_status,
                cs.be_gross_commission,
                CASE
                    WHEN cs.tags ILIKE '%%listingside%%' AND cs.tags ILIKE '%%sellingside%%'
                        THEN scn.officegrosscommissiononsale
                    WHEN cs.tags ILIKE '%%listingside%%'
                        THEN COALESCE(scn.listingcommissionamount, scn.officegrosscommissiononsale)
                    WHEN cs.tags ILIKE '%%sellingside%%'
                        THEN COALESCE(scn.salecommissionamount, scn.officegrosscommissiononsale)
                    ELSE COALESCE(scn.salecommissionamount, scn.officegrosscommissiononsale)
                END AS ss_gross_commission,
                TRUE AS agent_level_flag,
                CASE
                    WHEN cs.skyslopefileid IS NULL OR s.saleguid IS NULL THEN TRUE
                    WHEN cs.be_amount IS DISTINCT FROM s.saleprice::numeric THEN TRUE
                    ELSE FALSE
                END AS amount_mismatch,
                CASE
                    WHEN cs.skyslopefileid IS NULL OR s.saleguid IS NULL THEN TRUE
                    WHEN cs.be_closed_date IS DISTINCT FROM s.escrowclosingdate::date THEN TRUE
                    ELSE FALSE
                END AS closed_date_mismatch,
                CASE
                    WHEN cs.skyslopefileid IS NULL OR s.saleguid IS NULL THEN TRUE
                    WHEN LOWER(s.status) = 'expired' THEN NULL
                    WHEN cs.be_transaction_status IS NULL OR s.status IS NULL THEN NULL
                    WHEN LOWER(cs.be_transaction_status) = LOWER(s.status) THEN FALSE
                    WHEN LOWER(cs.be_transaction_status) = 'cancelled'
                         AND LOWER(s.status) IN ('canceled/app', 'canceled/pend')
                    THEN FALSE
                    WHEN LOWER(cs.be_transaction_status) = 'closed'
                         AND LOWER(s.status) IN ('archived', 'closed')
                    THEN FALSE
                    ELSE TRUE
                END AS transaction_status_mismatch,
                CASE
                    WHEN cs.skyslopefileid IS NULL OR s.saleguid IS NULL THEN TRUE
                    WHEN cs.tags ILIKE '%%listingside%%' AND cs.tags ILIKE '%%sellingside%%'
                        THEN CASE
                            WHEN scn.officegrosscommissiononsale IS NULL
                              OR cs.be_gross_commission IS NULL
                              OR scn.officegrosscommissiononsale = 0
                              OR cs.be_gross_commission = 0
                            THEN NULL
                            WHEN ROUND(scn.officegrosscommissiononsale::numeric, 2)
                                 IS DISTINCT FROM ROUND(cs.be_gross_commission::numeric, 2)
                            THEN TRUE
                            ELSE FALSE
                        END
                    WHEN cs.tags ILIKE '%%listingside%%'
                        THEN CASE
                            WHEN COALESCE(scn.listingcommissionamount, scn.officegrosscommissiononsale) IS NULL
                              OR cs.be_gross_commission IS NULL
                              OR COALESCE(scn.listingcommissionamount, scn.officegrosscommissiononsale) = 0
                              OR cs.be_gross_commission = 0
                            THEN NULL
                            WHEN ROUND(COALESCE(scn.listingcommissionamount, scn.officegrosscommissiononsale)::numeric, 2)
                                 IS DISTINCT FROM ROUND(cs.be_gross_commission::numeric, 2)
                            THEN TRUE
                            ELSE FALSE
                        END
                    ELSE
                        CASE
                            WHEN COALESCE(scn.salecommissionamount, scn.officegrosscommissiononsale) IS NULL
                              OR cs.be_gross_commission IS NULL
                              OR COALESCE(scn.salecommissionamount, scn.officegrosscommissiononsale) = 0
                              OR cs.be_gross_commission = 0
                            THEN NULL
                            WHEN ROUND(COALESCE(scn.salecommissionamount, scn.officegrosscommissiononsale)::numeric, 2)
                                 IS DISTINCT FROM ROUND(cs.be_gross_commission::numeric, 2)
                            THEN TRUE
                            ELSE FALSE
                        END
                END AS gross_commission_mismatch,
                CASE
                    WHEN cs.skyslopefileid IS NULL OR s.saleguid IS NULL THEN TRUE
                    ELSE FALSE
                END AS no_skyslope_record
            FROM combined_source cs
            LEFT JOIN sale s
                ON cs.skyslopefileid ~* '^[0-9a-f-]{36}$'
               AND s.saleguid = cs.skyslopefileid::uuid
            LEFT JOIN sale_commission scn
                ON scn.saleguid = s.saleguid
        )
    """

    agent_list_query = """
        WITH brokerhold_agent_names AS (
            SELECT DISTINCT LOWER(TRIM(agent_name)) AS agent_name
            FROM (
                SELECT regexp_split_to_table(be.buying_agent_name, '\\s*,\\s*') AS agent_name
                FROM brokerage_engine be
                WHERE be.tags ILIKE '%brokerhold%'
                  AND be.buying_agent_name IS NOT NULL
                  AND TRIM(be.buying_agent_name) <> ''

                UNION ALL

                SELECT regexp_split_to_table(oit.agents, '\\s*,\\s*') AS agent_name
                FROM otherincome_transactions oit
                WHERE oit.tags ILIKE '%brokerhold%'
                  AND oit.agents IS NOT NULL
                  AND TRIM(oit.agents) <> ''
            ) x
            WHERE agent_name IS NOT NULL
              AND TRIM(agent_name) <> ''
        )
        SELECT COALESCE(
            array_agg(agent_name ORDER BY agent_name),
            ARRAY[]::text[]
        ) AS all_agent_names
        FROM brokerhold_agent_names;
    """

    summary_query = f"""
        {common_cte}
        SELECT
            COUNT(*) AS total_pre_cda,
            COUNT(*) FILTER (
                WHERE
                    no_skyslope_record = TRUE
                    OR amount_mismatch = TRUE
                    OR closed_date_mismatch = TRUE
                    OR transaction_status_mismatch = TRUE
                    OR gross_commission_mismatch = TRUE
            ) AS transaction_level_flag_count,
            COUNT(*) FILTER (
                WHERE agent_level_flag = TRUE
            ) AS agent_level_flag_count,
            COUNT(*) FILTER (
                WHERE
                    agent_level_flag = TRUE
                    OR no_skyslope_record = TRUE
                    OR amount_mismatch = TRUE
                    OR closed_date_mismatch = TRUE
                    OR transaction_status_mismatch = TRUE
                    OR gross_commission_mismatch = TRUE
            ) AS flagged_count
        FROM base;
    """

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(summary_query)
        summary = cur.fetchone()

        cur.execute(agent_list_query)
        agent_list_result = cur.fetchone()

    search_clause = ""
    if search:
        search_clause = """
            (
                CAST(b.transaction_id AS TEXT) ILIKE %s
                OR b.property_address ILIKE %s
                OR COALESCE(b.agent_name, '') ILIKE %s
                OR COALESCE(b.skyslopefileid, '') ILIKE %s
                OR COALESCE(b.email, '') ILIKE %s
            )
        """
        search_term = f"%{search}%"
        params.extend([search_term, search_term, search_term, search_term, search_term])

    agent_clause = ""
    if agent_name:
        agent_clause = """
            (
                EXISTS (
                    SELECT 1
                    FROM regexp_split_to_table(b.agent_name, '\\s*,\\s*') AS split_agent(agent_name)
                    WHERE LOWER(TRIM(split_agent.agent_name)) = LOWER(TRIM(%s))
                      AND TRIM(split_agent.agent_name) <> ''
                )
            )
        """
        params.append(agent_name)

    filter_clause = ""
    if filter == "transaction_flag":
        filter_clause = """
            (
                b.no_skyslope_record = TRUE
                OR b.amount_mismatch = TRUE
                OR b.closed_date_mismatch = TRUE
                OR b.transaction_status_mismatch = TRUE
                OR b.gross_commission_mismatch = TRUE
            )
        """
    elif filter == "agent_flag":
        filter_clause = """
            (
                b.agent_level_flag = TRUE
            )
        """
    elif filter == "all_flagged":
        filter_clause = """
            (
                b.agent_level_flag = TRUE
                OR b.no_skyslope_record = TRUE
                OR b.amount_mismatch = TRUE
                OR b.closed_date_mismatch = TRUE
                OR b.transaction_status_mismatch = TRUE
                OR b.gross_commission_mismatch = TRUE
            )
        """

    where_parts = []
    if filter_clause:
        where_parts.append(filter_clause)
    if agent_clause:
        where_parts.append(agent_clause)
    if search_clause:
        where_parts.append(search_clause)

    final_where = ""
    if where_parts:
        final_where = "WHERE " + " AND ".join(where_parts)

    count_query = f"""
        {common_cte}
        SELECT COUNT(*) AS count
        FROM base b
        {final_where};
    """

    data_query = f"""
        {common_cte}
        SELECT
            b.source_table,
            b.transaction_id,
            b.skyslopefileid,
            b.property_address,
            b.tags,
            b.agent_name,
            b.email,
            b.agent_level_flag,
            b.no_skyslope_record,
            b.be_amount,
            b.ss_sale_price,
            b.amount_mismatch,
            b.be_closed_date,
            b.ss_closed_date,
            b.closed_date_mismatch,
            b.be_transaction_status,
            b.ss_transaction_status,
            b.transaction_status_mismatch,
            b.be_gross_commission,
            b.ss_gross_commission,
            b.gross_commission_mismatch
        FROM base b
        {final_where}
        ORDER BY b.agent_name, b.transaction_id
        LIMIT %s OFFSET %s;
    """

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(count_query, params)
        total_count = cur.fetchone()["count"]

        cur.execute(data_query, params + [limit, offset])
        rows = cur.fetchall()

    reshaped_rows = []
    for row in rows:
        transaction_level_flag = (
            row["no_skyslope_record"] is True
            or row["amount_mismatch"] is True
            or row["closed_date_mismatch"] is True
            or row["transaction_status_mismatch"] is True
            or row["gross_commission_mismatch"] is True
        )

        reshaped_rows.append({
            "record_id": f"{row['source_table']}:{row['transaction_id']}:{row['skyslopefileid'] or 'null'}",
            "source_table": row["source_table"],
            "transaction_id": row["transaction_id"],
            "skyslopefileid": row["skyslopefileid"],
            "property_address": row["property_address"],
            "tags": row["tags"],
            "agent_name": row["agent_name"],
            "email": row["email"],
            "agent_level_flag": row["agent_level_flag"],
            "transaction_level_flag": transaction_level_flag,
            "no_skyslope_record": row["no_skyslope_record"],
            "be_amount": row["be_amount"],
            "ss_sale_price": row["ss_sale_price"],
            "amount_mismatch": row["amount_mismatch"],
            "be_closed_date": row["be_closed_date"],
            "ss_closed_date": row["ss_closed_date"],
            "closed_date_mismatch": row["closed_date_mismatch"],
            "be_transaction_status": row["be_transaction_status"],
            "ss_transaction_status": row["ss_transaction_status"],
            "transaction_status_mismatch": row["transaction_status_mismatch"],
            "be_gross_commission": row["be_gross_commission"],
            "ss_gross_commission": row["ss_gross_commission"],
            "gross_commission_mismatch": row["gross_commission_mismatch"],
        })

    reshaped_rows = await fetch_ar_balance(reshaped_rows, conn)

    total_pages = (total_count + limit - 1) // limit

    return {
        "filter": filter,
        "selected_agent_name": agent_name,
        "summary": {
            "count": total_count,
            "total_pre_cda": summary["total_pre_cda"],
            "transaction_level_flag_count": summary["transaction_level_flag_count"],
            "agent_level_flag_count": summary["agent_level_flag_count"],
            "flagged_count": summary["flagged_count"],
            "all_agent_names": agent_list_result["all_agent_names"],
        },
        "page": page,
        "page_size": limit,
        "total_pages": total_pages,
        "data": reshaped_rows,
    }


@router.get("/pre-cda/listing")
async def get_pre_cda(
    filter: PreCDAFilter = Query(default=PreCDAFilter.all),
    page: int = Query(default=1, ge=1),
    search: str | None = Query(default=None),
    agent_name: str | None = Query(default=None),
    conn=Depends(get_db)
):
    return await fetch_pre_cda_data(filter.value, page, search, agent_name, conn)