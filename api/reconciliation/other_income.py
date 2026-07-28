from fastapi import APIRouter, Query, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from db import get_db

router = APIRouter()

@router.get("/other_income")
def other_income_listing(db: Session = Depends(get_db)):
    query = """
    SELECT
        oit.transaction_identifier_transactionid AS transactionid,
        oit.transaction_identifier_transactionguid AS transactionguid,
        oit.listingguid AS listingguid,
        oit.property_address AS propertyaddress,
        oit.transaction_status AS transaction_status,
        oit.address_line1 AS address_line1,
        oit.address_line2 AS address_line2,
        oit.city AS city,
        oit.state AS state,
        oit.zip AS zip,
        oit.property_type AS property_type,
        oit.property_subtype AS property_subtype,
        oit.office AS office,
        oit.income_type AS income_type,
        oit.income_received_date AS income_received_date,
        oit.income_received AS income_received,
        oit.agents AS agents,
        oit.gross_commission AS gross_commission,
        oit.agent_net AS agent_net,
        oit.brokerage_net AS brokerage_net,
        oit.agents_identifier AS agents_identifier,
        oit.client_type AS client_type,
        oit.client_name AS client_name,
        oit.client_phone AS client_phone,
        oit.client_email AS client_email,
        oit.tags AS tags,
        oit.effective_at AS effective_at,
        oit.finalized_date AS finalized_date,
        oit.transaction_specialist AS transaction_specialist,

        oit.skyslopefileid AS otherincome_skyslopefileid,
        be.skyslopefileid AS brokerageengine_skyslopefileid,
        COALESCE(oit.skyslopefileid, be.skyslopefileid) AS skyslopefileid

    FROM otherincome_transactions oit
    LEFT JOIN brokerage_engine be
        ON oit.property_address = be.property_address

    WHERE
        oit.skyslopefileid IS NOT NULL
        OR (
            oit.skyslopefileid IS NULL
            AND be.skyslopefileid IS NOT NULL
        )

    ORDER BY oit.transaction_identifier_transactionid;
    """

    rows = db.execute(text(query)).mappings().all()

    return {
        "count": len(rows),
        "data": [dict(row) for row in rows]
    }