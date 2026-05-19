from fastapi import APIRouter, Query
from typing import Optional, List
from services.comparison import compare_listing_price
from db import get_conn

router = APIRouter()

@router.get("/book-closing/listing")
def get_transactions_with_stage(
    transaction_specialist: Optional[str] = Query(None),
    buying_agent_name: Optional[str] = Query(None),
    stage_name: Optional[List[str]] = Query(None),
    cda_sent: Optional[bool] = Query(None)
):
    conn = get_conn()

    try:
        cur = conn.cursor()

        query = """
            WITH base AS (
                SELECT
                    be.transaction_identifier_transactionid AS transaction_id,
                    be.property_address,
                    be.state,
                    be.tags,
                    be.buying_agent_name,
                    be.closed_date,
                    be.transaction_specialist,
                    be.transaction_status,
                    s.stageid AS stage_id,
                    stage.name AS stage_name,

                    be.sale_price AS be_sale_price,
                    s.saleprice AS ss_sale_price,
                    be.contract_date AS be_contract_date,
                    s.contractacceptancedate AS ss_contract_date,
                    be.listing_price AS be_listing_price,
                    s.listingprice AS ss_listing_price,
                    be.total_gross_commission AS be_gross_commission,
                    scn.officegrosscommissiononsale AS ss_gross_commission,

                    CASE
                        WHEN be.sale_price IS NULL OR s.saleprice IS NULL THEN NULL
                        WHEN be.sale_price = s.saleprice THEN false
                        ELSE true
                    END AS sale_price_mismatch,

                    CASE
                        WHEN be.closed_date IS NULL OR s.escrowclosingdate IS NULL THEN NULL
                        WHEN be.closed_date = s.escrowclosingdate THEN false
                        ELSE true
                    END AS closed_date_mismatch,

                    CASE
                        WHEN be.contract_date IS NULL OR s.contractacceptancedate IS NULL THEN NULL
                        WHEN be.contract_date = s.contractacceptancedate THEN false
                        ELSE true
                    END AS contract_date_mismatch,

                    CASE
                        WHEN be.transaction_status IS NULL OR s.status IS NULL THEN NULL
                        WHEN LOWER(s.status) = 'expired' THEN NULL
                        WHEN LOWER(be.transaction_status) = LOWER(s.status) THEN false
                        WHEN LOWER(be.transaction_status) = 'cancelled'
                             AND LOWER(s.status) IN ('canceled/app', 'canceled/pend')
                        THEN false
                        ELSE true
                    END AS transaction_status_mismatch,

                    CASE
                        WHEN be.total_gross_commission IS NULL
                             OR scn.officegrosscommissiononsale IS NULL
                        THEN NULL
                        WHEN be.total_gross_commission = 0
                             OR scn.officegrosscommissiononsale = 0
                        THEN NULL
                        WHEN be.total_gross_commission = scn.officegrosscommissiononsale
                        THEN 'match'
                        ELSE 'mismatch'
                    END AS gross_commission_mismatch

                FROM brokerage_engine be
                LEFT JOIN sale s
                    ON be.skyslopefileid = s.saleguid
                LEFT JOIN sale_commission scn
                    ON scn.saleguid = s.saleguid
                LEFT JOIN stage
                    ON s.stageid = stage.stageid
            )
            SELECT *
            FROM base
        """

        params = []
        conditions = []

        if transaction_specialist:
            conditions.append("transaction_specialist = %s")
            params.append(transaction_specialist.strip())

        if buying_agent_name:
            conditions.append("""
                LOWER(%s) = ANY(
                    string_to_array(
                        LOWER(regexp_replace(buying_agent_name, '\\s*,\\s*', ',', 'g')),
                        ','
                    )
                )
            """)
            params.append(buying_agent_name.strip())

        if stage_name:
            placeholders = ", ".join(["LOWER(%s)"] * len(stage_name))
            conditions.append(f"LOWER(stage_name) IN ({placeholders})")
            params.extend([stage.strip() for stage in stage_name])

        if cda_sent is True:
            conditions.append("tags ILIKE %s")
            params.append("%CDASent%")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY closed_date DESC NULLS LAST, transaction_id DESC"

        cur.execute(query, params)
        rows = cur.fetchall()

        columns = [
            "transaction_id",
            "property_address",
            "state",
            "tags",
            "buying_agent_name",
            "closed_date",
            "transaction_specialist",
            "transaction_status",
            "stage_id",
            "stage_name",
            "be_sale_price",
            "ss_sale_price",
            "be_contract_date",
            "ss_contract_date",
            "be_listing_price",
            "ss_listing_price",
            "be_gross_commission",
            "ss_gross_commission",
            "sale_price_mismatch",
            "closed_date_mismatch",
            "contract_date_mismatch",
            "transaction_status_mismatch",
            "gross_commission_mismatch"
        ]

        result = []
        for row in rows:
            row_dict = dict(zip(columns, row))

            is_stale = (
                row_dict["sale_price_mismatch"] is True
                or row_dict["closed_date_mismatch"] is True
                or row_dict["contract_date_mismatch"] is True
                or row_dict["transaction_status_mismatch"] is True
                or row_dict["gross_commission_mismatch"] == "mismatch"
            )

            row_dict["is_stale"] = is_stale
            result.append(row_dict)

        return {
            "count": len(result),
            "data": result
        }

    finally:
        conn.close()