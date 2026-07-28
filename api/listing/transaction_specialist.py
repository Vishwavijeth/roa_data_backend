from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from db import get_db

router = APIRouter()

@router.get("/transaction_specialist_listing")
def transaction_specialist_listing(db: Session = Depends(get_db)):
    query = """
    SELECT
        be.transaction_identifier_transactionid AS transactionid,
        be.property_address AS propertyaddress,

        be.sale_price AS be_sale_price,
        be.listing_price AS listing_price,
        be.closed_date AS be_closed_date,

        be.transaction_status AS be_workflow_status,
        
        be.transaction_specialist AS transaction_specialist,
        be.skyslopefileid AS skyslopefileid

    FROM brokerage_engine be

    ORDER BY be.transaction_identifier_transactionid;
    """

    rows = db.execute(text(query)).mappings().all()

    return {
        "count": len(rows),
        "data": [dict(row) for row in rows]
    }