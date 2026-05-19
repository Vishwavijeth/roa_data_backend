from fastapi import HTTPException, APIRouter, Query
from services.engine import run_brokerage_engine, load_data
from services.loaders import get_be_sync

router = APIRouter()

@router.get("/brokerage_engine")
def brokerage_engine(
    brokerhold: bool = Query(
        default=False,
        description="Filter records having brokerhold in tags"
    )
):
    data = run_brokerage_engine(brokerhold)
    sync_info = get_be_sync()

    return {
        "sync_info": sync_info,
        "data": data
    }

def norm(x):
    return str(x or "").replace("\u00A0", "").strip().lower()

@router.get("/brokerage_engine/detail")
def brokerage_detail(transactionid: str):
    sales, be_data = load_data()

    txn = norm(transactionid)

    # find BE record
    be_record = next(
        (b for b in be_data
         if norm(b.get("transaction_identifier_transactionid")) == txn),
        None
    )

    if not be_record:
        raise HTTPException(status_code=404, detail="Transaction not found")

    skyslopefileid = be_record.get("skyslopefileid")

    # find sales record (FIXED KEY LOGIC)
    sale_record = next(
        (s for s in sales
         if norm(s.get("saleguid")) == norm(skyslopefileid)),
        None
    )
    match = True
    if sale_record:
        match = norm(skyslopefileid) == norm(sale_record.get("saleguid"))
    else:
        match = False

    return {
        "transactionid": transactionid,
        "brokerage_engine": {
            "property_address": be_record.get("property_address"),
            "sale_price": be_record.get("sale_price"),
            "listing_price": be_record.get("listing_price"),
            "office": be_record.get("listing_office"),
            "buyer": be_record.get("buyer_name"),
            "seller": be_record.get("seller_name"),
            "buying_agent_name": be_record.get("buying_agent_name"),
            "contract_date": be_record.get("contract_date"),
            "closed_date": be_record.get("closed_date"),
            "tags": be_record.get("tags"),
            "status": be_record.get("transaction_status"),
            "transaction_specialist": be_record.get("transaction_specialist"),
            "skyslopefileid": skyslopefileid
        },
        "skyslope": {
            "match": match,
            "saleguid": sale_record.get("saleguid") if sale_record else None,
            "property_address": sale_record.get("propertyaddress") if sale_record else None,
            "listingprice": sale_record.get("listingprice") if sale_record else None,
            "saleprice": sale_record.get("saleprice") if sale_record else None,
            "mlsnumber": sale_record.get("mlsnumber") if sale_record else None,
            "seller": sale_record.get("seller_full_name") if sale_record else None,
            "buyer": sale_record.get("buyer_full_name") if sale_record else None,
            "buying_agent": sale_record.get("agent_full_name") if sale_record else None,
            "buying_agent_email": sale_record.get("agent_mail_id") if sale_record else None,
            "reviewer_full_name": sale_record.get("reviewer_full_name") if sale_record else None,
            "status": sale_record.get("status") if sale_record else None,
            "contractacceptancedate": sale_record.get("contractacceptancedate") if sale_record else None,
            "escrowclosingdate": sale_record.get("escrowclosingdate") if sale_record else None,
            "canceldate": sale_record.get("canceldate") if sale_record else None
        }
    }