from fastapi import APIRouter, HTTPException, Query
from db import get_conn
from services.loaders import get_skyslope_sync  

router = APIRouter()

def norm(x):
    return str(x or "").replace("\u00A0", "").strip().lower()

@router.get("/skyslope/sync_info")
def skyslope_sync_info():
    sync_info = get_skyslope_sync()

    return {
        "sync_info": sync_info
    }

@router.get("/skyslope_api")
def skyslope_api(
    page: int = Query(default=1, ge=1),

    from_close_date: str = Query(default=None),
    to_close_date: str = Query(default=None),

    from_contract_date: str = Query(default=None),
    to_contract_date: str = Query(default=None),

    status: str = Query(default=None),

    search: str = Query(default=None)
):
    conn = get_conn()
    cursor = conn.cursor()

    limit = 50
    offset = (page - 1) * limit

    # =====================================================
    # 1. BASE FILTER QUERY (NO JOINS)
    # =====================================================
    base_filter = """
        FROM sale s
        WHERE 1=1
    """

    params = []

    # ---------------- SALE FILTERS ----------------
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

    # ---------------- SEARCH (must include JOIN tables via EXISTS) ----------------
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

    # =====================================================
    # 2. COUNT (NOW SEARCH WORKS)
    # =====================================================
    count_query = "SELECT COUNT(*) " + base_filter

    cursor.execute(count_query, params)
    total_count = cursor.fetchone()[0]

    # =====================================================
    # 3. DATA QUERY (WITH JOINS FOR DISPLAY ONLY)
    # =====================================================
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

    data_query += base_filter.replace("FROM sale s", "")  # reuse same filters safely

    data_query += """
        ORDER BY s.escrowclosingdate DESC NULLS LAST
        LIMIT %s OFFSET %s
    """

    cursor.execute(data_query, params + [limit, offset])

    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    data = [dict(zip(columns, row)) for row in rows]

    return {
        "total_count": total_count,
        "data": data
    }

@router.get("/skyslope/detail")
def skyslope_detail(saleguid: str):
    conn = get_conn()
    cursor = conn.cursor()

    query = """
        SELECT
            s.saleguid,

            -- SKY SLOPE FIELDS
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
            ) AS seller,

            -- BROKERAGE ENGINE FIELDS
            be.transaction_identifier_transactionid::text AS transactionid,
            be.property_address,
            be.sale_price AS be_sale_price,
            be.listing_price AS be_listing_price,
            be.listing_office AS office,
            be.buying_agent_name,
            be.contract_date,
            be.closed_date,
            be.tags,
            be.transaction_status,
            be.transaction_specialist,
            be.skyslopefileid

        FROM sale s

        LEFT JOIN users u ON s.agentguid = u.userguid
        LEFT JOIN users r ON s.reviewerguid = r.userguid
        LEFT JOIN sale_property sp ON s.saleguid = sp.saleguid
        LEFT JOIN brokerage_engine be ON be.skyslopefileid = s.saleguid

        WHERE s.saleguid = %s
    """

    cursor.execute(query, [saleguid])
    row = cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Sale not found")

    columns = [desc[0] for desc in cursor.description]
    data = dict(zip(columns, row))

    return {
        "saleguid": data["saleguid"],

        # ---------------- SKY SLOPE ----------------
        "skyslope": {
            "saleguid": data["skyslope_saleguid"],
            "propertyaddress": data["propertyaddress"],
            "listingprice": data["listingprice"],
            "saleprice": data["saleprice"],
            "mlsnumber": data["mlsnumber"],
            "seller": data["seller"],
            "buyer": data["buyer"],
            "buyer_agent": data["buyer_agent_name"],
            "buyer_agent_email": data["buyer_agent_email"],
            "reviewer": data["reviewer"],
            "status": data["status"],
            "contractacceptancedate": data["contractacceptancedate"],
            "escrowclosingdate": data["escrowclosingdate"],
        },

        # ---------------- BROKERAGE ENGINE ----------------
        "brokerage_engine": {
            "transactionid": data["transactionid"],
            "property_address": data["property_address"],
            "sale_price": data["be_sale_price"],
            "listing_price": data["be_listing_price"],
            "office": data["office"],
            "buyer": data["buyer"],
            "seller": data["seller"],
            "buying_agent_name": data["buying_agent_name"],
            "contract_date": data["contract_date"],
            "closed_date": data["closed_date"],
            "tags": data["tags"],
            "status": data["transaction_status"],
            "transaction_specialist": data["transaction_specialist"],
            "skyslopefileid": data["skyslopefileid"]
        }
    }