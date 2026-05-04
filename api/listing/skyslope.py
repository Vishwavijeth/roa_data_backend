from fastapi import APIRouter, HTTPException
from services.engine import get_skyslope_data, load_data

router = APIRouter()

def norm(x):
    return str(x or "").replace("\u00A0", "").strip().lower()

@router.get("/skyslope_api")
def skyslope_api():
    return get_skyslope_data()

@router.get("/skyslope/detail")
def skyslope_detail(saleguid: str):
    sales, be_data = load_data()

    sg = norm(saleguid)

    # 1. find SkySlope record
    sale_record = next(
        (s for s in sales if norm(s.get("saleguid")) == sg),
        None
    )

    if not sale_record:
        raise HTTPException(status_code=404, detail="Sale not found")

    # 2. extract skyslopefileid from BE side mapping logic
    be_record = next(
        (b for b in be_data if norm(b.get("skyslopefileid")) == sg),
        None
    )

    # 3. fallback: if BE is not directly mapped by saleguid,
    # try reverse mapping (important for your case)
    if not be_record:
        be_record = next(
            (
                b for b in be_data
                if norm(b.get("skyslopefileid")) == sg
            ),
            None
        )

    return {
        "saleguid": sg,

        # ---------------- SKY SLOPE ----------------
        "skyslope": {
            "saleguid": sale_record.get("saleguid"),
            "propertyaddress": sale_record.get("propertyaddress"),
            "listingprice": sale_record.get("listingprice"),
            "saleprice": sale_record.get("saleprice"),
            "mlsnumber": sale_record.get("mlsnumber"),
            "seller": sale_record.get("seller_full_name"),
            "buyer": sale_record.get("buyer_full_name"),
            "buyer_agent": sale_record.get("agent_full_name"),
            "buyer_agent_email": sale_record.get("agent_mail_id"),
            "reviewer": sale_record.get("reviewer_full_name"),
            "status": sale_record.get("status"),
            "contractacceptancedate": sale_record.get("contractacceptancedate"),
            "escrowclosingdate": sale_record.get("escrowclosingdate"),
            "canceldate": sale_record.get("canceldate")
        },

        # ---------------- BROKERAGE ENGINE ----------------
        "brokerage_engine": {
            "transactionid": be_record.get("transaction_identifier_transactionid") if be_record else None,
            "property_address": be_record.get("property_address") if be_record else None,
            "sale_price": be_record.get("sale_price") if be_record else None,
            "listing_price": be_record.get("listing_price") if be_record else None,
            "office": be_record.get("listing_office") if be_record else None,
            "buyer": be_record.get("buyer_name"),
            "seller": be_record.get("seller_name"),
            "buying_agent_name": be_record.get("buying_agent_name") if be_record else None,
            "contract_date": be_record.get("contract_date") if be_record else None,
            "closed_date": be_record.get("closed_date") if be_record else None,
            "tags": be_record.get("tags") if be_record else None,
            "transaction_specialist": be_record.get("transaction_specialist") if be_record else None,
            "skyslopefileid": be_record.get("skyslopefileid") if be_record else None
        }
    }