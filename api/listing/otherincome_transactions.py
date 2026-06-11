from typing import List, Optional
from fastapi import Query, HTTPException, APIRouter, Depends
from db import get_db

router = APIRouter()

@router.get("/otherincome_transactions")
def otherincome_transactions(
    page: int = Query(default=1, ge=1),
    from_income_received_date: str = Query(default=None),
    to_income_received_date: str = Query(default=None),
    from_finalized_date: str = Query(default=None),
    to_finalized_date: str = Query(default=None),
    status: Optional[List[str]] = Query(default=None),
    income_type: Optional[List[str]] = Query(default=None),
    search: str = Query(default=None),
    conn=Depends(get_db)
):
    cursor = conn.cursor()

    limit = 50
    offset = (page - 1) * limit

    base_query = """
        FROM otherincome_transactions
        WHERE 1=1
    """

    params = []

    if status:
        status_placeholders = ", ".join(["%s"] * len(status))
        base_query += f" AND LOWER(transaction_status) IN ({status_placeholders})"
        params.extend([s.lower() for s in status])

    if income_type:
        income_type_placeholders = ", ".join(["%s"] * len(income_type))
        base_query += f" AND LOWER(income_type) IN ({income_type_placeholders})"
        params.extend([i.lower() for i in income_type])

    if from_income_received_date:
        base_query += " AND income_received_date >= %s"
        params.append(from_income_received_date)

    if to_income_received_date:
        base_query += " AND income_received_date <= %s"
        params.append(to_income_received_date)

    if from_finalized_date:
        base_query += " AND finalized_date >= %s"
        params.append(from_finalized_date)

    if to_finalized_date:
        base_query += " AND finalized_date <= %s"
        params.append(to_finalized_date)

    if search:
        search_value = f"%{search.lower()}%"
        base_query += """
            AND (
                LOWER(transaction_identifier_transactionid::text) LIKE %s
                OR LOWER(COALESCE(property_address, '')) LIKE %s
                OR LOWER(COALESCE(client_name, '')) LIKE %s
                OR LOWER(COALESCE(client_email, '')) LIKE %s
                OR LOWER(COALESCE(agents, '')) LIKE %s
                OR LOWER(COALESCE(transaction_specialist, '')) LIKE %s
                OR LOWER(COALESCE(income_type, '')) LIKE %s
            )
        """
        params.extend([
            search_value, search_value, search_value, search_value,
            search_value, search_value, search_value
        ])

    count_query = "SELECT COUNT(*) " + base_query
    cursor.execute(count_query, params)
    total_count = cursor.fetchone()[0]

    data_query = """
        SELECT
            transaction_identifier_transactionid AS transactionid,
            property_address,
            income_type,
            income_received_date,
            income_received,
            gross_commission,
            agent_net,
            brokerage_net,
            client_name,
            agents,
            finalized_date,
            transaction_specialist,
            transaction_status AS status,
            skyslopefileid
    """ + base_query

    data_query += " ORDER BY finalized_date DESC NULLS LAST, income_received_date DESC NULLS LAST"
    data_query += " LIMIT %s OFFSET %s"

    data_params = params + [limit, offset]

    cursor.execute(data_query, data_params)

    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    data = [dict(zip(columns, row)) for row in rows]

    filter_query = """
        SELECT DISTINCT transaction_status
        FROM otherincome_transactions
        WHERE transaction_status IS NOT NULL
        ORDER BY transaction_status
    """
    cursor.execute(filter_query)
    status_options = [row[0] for row in cursor.fetchall()]

    income_type_query = """
        SELECT DISTINCT income_type
        FROM otherincome_transactions
        WHERE income_type IS NOT NULL
        ORDER BY income_type
    """
    cursor.execute(income_type_query)
    income_type_options = [row[0] for row in cursor.fetchall()]

    return {
        "total_count": total_count,
        "filters": {
            "status": status_options,
            "income_type": income_type_options
        },
        "data": data
    }


@router.get("/otherincome_transactions/detail")
def otherincome_detail(transactionid: str, conn=Depends(get_db)):
    cursor = conn.cursor()

    query = """
        SELECT
            oit.skyslopefileid,
            oit.listingguid,
            oit.transaction_identifier_transactionid,
            oit.property_address,
            oit.income_type,
            oit.income_received_date,
            oit.income_received,
            oit.gross_commission,
            oit.agent_net,
            oit.brokerage_net,
            oit.agents,
            oit.tags,
            oit.effective_at,
            oit.finalized_date,
            oit.transaction_specialist,
            oit.transaction_status,

            s.saleguid,
            s.listingprice,
            s.saleprice,
            s.mlsnumber,
            s.status,
            s.contractacceptancedate,
            s.escrowclosingdate,
            s.reviewerguid,
            s.agentguid,
            s.canceldate,

            COALESCE(r.firstname || ' ' || r.lastname, '') AS reviewer_full_name,

            COALESCE(
                (
                    SELECT TRIM(COALESCE(uu.firstname, '') || ' ' || COALESCE(uu.lastname, ''))
                    FROM users uu
                    WHERE uu.userguid = s.agentguid
                ),
                ''
            ) AS skyslope_buying_agent_name,

            CONCAT_WS(', ',
                CONCAT_WS(' ', sp.streetnumber, sp.streetaddress),
                sp.city,
                sp.state,
                sp.zip
            ) AS propertyaddress,

            COALESCE(scn.officeGrossCommissionOnSale, 0) AS officegrosscommissiononsale

        FROM otherincome_transactions oit

        LEFT JOIN sale s
            ON oit.skyslopefileid = s.saleguid

        LEFT JOIN users r
            ON s.reviewerguid = r.userguid

        LEFT JOIN sale_property sp
            ON s.saleguid = sp.saleguid

        LEFT JOIN sale_commission scn
            ON scn.saleguid = s.saleguid

        WHERE oit.transaction_identifier_transactionid = %s
    """

    cursor.execute(query, (transactionid,))
    row = cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Transaction not found")

    columns = [desc[0] for desc in cursor.description]
    data = dict(zip(columns, row))

    skyslope_match = data.get("saleguid") is not None

    return {
        "transactionid": transactionid,
        "otherincome_transactions": {
            "property_address": data.get("property_address"),
            "income_type": data.get("income_type"),
            "income_received_date": data.get("income_received_date"),
            "income_received": data.get("income_received"),
            "gross_commission": data.get("gross_commission"),
            "agent_net": data.get("agent_net"),
            "brokerage_net": data.get("brokerage_net"),
            "agents": data.get("agents"),
            "tags": data.get("tags"),
            "effective_at": data.get("effective_at"),
            "finalized_date": data.get("finalized_date"),
            "status": data.get("transaction_status"),
            "transaction_specialist": data.get("transaction_specialist"),
            "skyslopefileid": data.get("skyslopefileid")
        },
        "skyslope": {
            "match": skyslope_match,
            "saleguid": data.get("saleguid"),
            "property_address": data.get("propertyaddress"),
            "listingprice": data.get("listingprice"),
            "saleprice": data.get("saleprice"),
            "mlsnumber": data.get("mlsnumber"),
            "buying_agent": data.get("skyslope_buying_agent_name"),
            "reviewer_full_name": data.get("reviewer_full_name"),
            "status": data.get("status"),
            "contractacceptancedate": data.get("contractacceptancedate"),
            "escrowclosingdate": data.get("escrowclosingdate"),
            "canceldate": data.get("canceldate"),
            "officegrosscommissiononsale": data.get("officegrosscommissiononsale")
        }
    }