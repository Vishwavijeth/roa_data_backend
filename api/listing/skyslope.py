from fastapi import APIRouter, HTTPException, Query, Depends
from db import get_db
from services.loaders import get_skyslope_sync  

router = APIRouter()

def norm(x):
    return str(x or "").replace("\u00A0", "").strip().lower()

@router.get("/skyslope/sync_info")
def skyslope_sync_info(conn=Depends(get_db)):
    sync_info = get_skyslope_sync(conn)

    return {
        "sync_info": sync_info,
    }

@router.get("/skyslope_api")
def skyslope_api(
    page: int = Query(default=1, ge=1),
    from_close_date: str = Query(default=None),
    to_close_date: str = Query(default=None),
    from_contract_date: str = Query(default=None),
    to_contract_date: str = Query(default=None),
    status: str = Query(default=None),
    search: str = Query(default=None),
    conn=Depends(get_db)
):
    cursor = conn.cursor()

    limit = 50
    offset = (page - 1) * limit

    base_filter = """
        FROM sale s
        WHERE 1=1
    """

    params = []

    if status:
        base_filter += " AND LOWER(s.status) = %s"
        params.append(status.lower())

    if from_close_date:
        base_filter += " AND s.escrowclosingdate >= %s"
        params.append(from_close_date)

    if to_close_date:
        base_filter += " AND s.escrowclosingdate <= %s"
        params.append(to_close_date)

    if from_contract_date:
        base_filter += " AND s.contractacceptancedate >= %s"
        params.append(from_contract_date)

    if to_contract_date:
        base_filter += " AND s.contractacceptancedate <= %s"
        params.append(to_contract_date)

    if search:
        search_value = f"%{search.lower()}%"

        base_filter += """
            AND (
                EXISTS (
                    SELECT 1
                    FROM brokerage_engine be
                    WHERE be.skyslopefileid = s.saleguid
                    AND LOWER(be.transaction_identifier_transactionid::text) LIKE %s
                )
                OR EXISTS (
                    SELECT 1
                    FROM sale_property sp
                    WHERE sp.saleguid = s.saleguid
                    AND LOWER(
                        CONCAT_WS(', ',
                            CONCAT_WS(' ', sp.streetnumber, sp.streetaddress),
                            sp.city,
                            sp.state,
                            sp.zip
                        )
                    ) LIKE %s
                )
                OR LOWER(s.saleguid::text) LIKE %s
            )
        """
        params.extend([search_value, search_value, search_value])

    count_query = "SELECT COUNT(*) " + base_filter
    cursor.execute(count_query, params)
    total_count = cursor.fetchone()[0]

    data_query = """
        SELECT
            s.saleguid,
            CONCAT_WS(', ',
                CONCAT_WS(' ', sp.streetnumber, sp.streetaddress),
                sp.city,
                sp.state,
                sp.zip
            ) AS propertyaddress,
            s.escrowclosingdate AS close_date,
            s.status,
            CASE
                WHEN be.transaction_identifier_transactionid IS NULL
                THEN 'No related BE data'
                ELSE be.transaction_identifier_transactionid::text
            END AS transaction_id,
            NULLIF(
                COALESCE(
                    (
                        SELECT STRING_AGG(
                            TRIM(
                                COALESCE(sc.firstname, '') || ' ' ||
                                COALESCE(sc.lastname, '')
                            ),
                            ', '
                        )
                        FROM sale_contact sc
                        WHERE sc.saleguid = s.saleguid
                        AND LOWER(sc.role) = 'buyer'
                    ),
                    ''
                ),
                ''
            ) AS buyer_name,
            NULLIF(
                (
                    SELECT TRIM(COALESCE(u.firstname, '') || ' ' || COALESCE(u.lastname, ''))
                    FROM users u
                    WHERE u.userguid = s.agentguid
                ),
                ''
            ) AS buyer_agent_name,
            s.contractacceptancedate AS contract_date,
            NULLIF(
                TRIM(COALESCE(r.firstname, '') || ' ' || COALESCE(r.lastname, '')),
                ''
            ) AS reviewer
        FROM sale s
        LEFT JOIN users u ON s.agentguid = u.userguid
        LEFT JOIN users r ON s.reviewerguid = r.userguid
        LEFT JOIN sale_property sp ON s.saleguid = sp.saleguid
        LEFT JOIN brokerage_engine be ON be.skyslopefileid = s.saleguid
    """

    data_query += base_filter.replace("FROM sale s", "")

    data_query += """
        ORDER BY s.escrowclosingdate DESC NULLS LAST
        LIMIT %s OFFSET %s
    """

    cursor.execute(data_query, params + [limit, offset])

    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    data = [dict(zip(columns, row)) for row in rows]

    status_query = """
        SELECT DISTINCT status
        FROM sale
        WHERE status IS NOT NULL
        ORDER BY status
    """
    cursor.execute(status_query)
    status_list = [row[0] for row in cursor.fetchall()]

    return {
        "total_count": total_count,
        "filters": {
            "status_list": status_list
        },
        "data": data
    }

@router.get("/skyslope/detail")
def skyslope_detail(saleguid: str, conn=Depends(get_db)):
    cursor = conn.cursor()

    skyslope_query = """
        SELECT
            s.saleguid,
            s.saleguid AS skyslope_saleguid,
            CONCAT_WS(', ',
                CONCAT_WS(' ', sp.streetnumber, sp.streetaddress),
                sp.city,
                sp.state,
                sp.zip
            ) AS propertyaddress,
            s.listingprice,
            s.saleprice,
            s.mlsnumber,
            s.contractacceptancedate,
            s.escrowclosingdate,
            s.status,

            COALESCE(u.firstname || ' ' || u.lastname, NULL) AS buyer_agent_name,
            COALESCE(u.email, NULL) AS buyer_agent_email,
            COALESCE(r.firstname || ' ' || r.lastname, NULL) AS reviewer,

            COALESCE(
                (
                    SELECT STRING_AGG(
                        TRIM(
                            COALESCE(sc.firstname, '') || ' ' ||
                            COALESCE(sc.lastname, '')
                        ),
                        ', '
                    )
                    FROM sale_contact sc
                    WHERE sc.saleguid = s.saleguid
                      AND LOWER(sc.role) = 'buyer'
                ),
                NULL
            ) AS buyer,

            COALESCE(
                (
                    SELECT STRING_AGG(
                        TRIM(
                            COALESCE(sc.firstname, '') || ' ' ||
                            COALESCE(sc.lastname, '')
                        ),
                        ', '
                    )
                    FROM sale_contact sc
                    WHERE sc.saleguid = s.saleguid
                      AND LOWER(sc.role) = 'seller'
                ),
                NULL
            ) AS seller
        FROM sale s
        LEFT JOIN users u ON s.agentguid = u.userguid
        LEFT JOIN users r ON s.reviewerguid = r.userguid
        LEFT JOIN sale_property sp ON s.saleguid = sp.saleguid
        WHERE s.saleguid = %s
    """

    cursor.execute(skyslope_query, [saleguid])
    row = cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Sale not found")

    columns = [desc[0] for desc in cursor.description]
    skyslope_data = dict(zip(columns, row))

    be_query = """
        SELECT
            be.transaction_identifier_transactionid::text AS transactionid,
            be.property_address,
            be.sale_price,
            be.listing_price,
            be.listing_office AS office,
            be.buyer_name,
            be.seller_name,
            be.buying_agent_name,
            be.contract_date,
            be.closed_date,
            be.tags,
            be.transaction_status,
            be.transaction_specialist,
            be.skyslopefileid,
            be.total_gross_commission,
            CASE
                WHEN LOWER(be.transaction_status) = 'cancelled'
                    THEN 'ignored_cancelled_duplicate'
                ELSE 'active'
            END AS record_role
        FROM brokerage_engine be
        WHERE be.skyslopefileid = %s
        ORDER BY
            CASE
                WHEN LOWER(be.transaction_status) = 'cancelled' THEN 2
                ELSE 1
            END,
            be.closed_date DESC NULLS LAST
    """

    cursor.execute(be_query, [saleguid])
    be_rows = cursor.fetchall()
    be_columns = [desc[0] for desc in cursor.description]
    brokerage_engine_records = [dict(zip(be_columns, r)) for r in be_rows]

    other_income_query = """
        SELECT
            oit.transaction_identifier_transactionid::text AS transactionid,
            oit.property_address,
            oit.income_type,
            oit.income_received_date,
            oit.income_received,
            oit.gross_commission,
            oit.agent_net,
            oit.brokerage_net,
            oit.agents,
            oit.client_type,
            oit.client_name,
            oit.client_phone,
            oit.client_email,
            oit.tags,
            oit.effective_at,
            oit.finalized_date,
            oit.transaction_specialist,
            oit.transaction_status,
            oit.skyslopefileid,
            'other_income' AS record_role
        FROM otherincome_transactions oit
        WHERE oit.skyslopefileid = %s
        ORDER BY oit.finalized_date DESC NULLS LAST, oit.income_received_date DESC NULLS LAST
    """

    cursor.execute(other_income_query, [saleguid])
    oi_rows = cursor.fetchall()
    oi_columns = [desc[0] for desc in cursor.description]
    otherincome_records = [dict(zip(oi_columns, r)) for r in oi_rows]

    return {
        "saleguid": skyslope_data["saleguid"],
        "skyslope": {
            "saleguid": skyslope_data["skyslope_saleguid"],
            "propertyaddress": skyslope_data["propertyaddress"],
            "listingprice": skyslope_data["listingprice"],
            "saleprice": skyslope_data["saleprice"],
            "mlsnumber": skyslope_data["mlsnumber"],
            "seller": skyslope_data["seller"],
            "buyer": skyslope_data["buyer"],
            "buyer_agent": skyslope_data["buyer_agent_name"],
            "buyer_agent_email": skyslope_data["buyer_agent_email"],
            "reviewer": skyslope_data["reviewer"],
            "status": skyslope_data["status"],
            "contractacceptancedate": skyslope_data["contractacceptancedate"],
            "escrowclosingdate": skyslope_data["escrowclosingdate"]
        },
        "brokerage_engine_records": brokerage_engine_records,
        "otherincome_transactions": otherincome_records,
    }