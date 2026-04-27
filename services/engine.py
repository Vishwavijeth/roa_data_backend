from .loaders import load_data
from fastapi import HTTPException
import json
from .field_registry import FIELD_MAP
from .comparison import (
    compare_values, 
    compare_names, 
    compare_buying_agent, 
    normalize_value,
    extract_be_status
    )


# ---------------- FILTER ----------------
def has_excluded_tag(tags):
    if not tags:
        return False

    tags_lower = str(tags).lower()

    return ("complete" in tags_lower) or ("revoked" in tags_lower)


# ---------------- BUILD LOOKUP (STRICT FILTER APPLIED HERE) ----------------
def build_lookup(be_data):
    """
    Step 1: remove excluded tags
    Step 2: build strict skyslopefileid map
    """

    filtered = [
        r for r in be_data
        if not has_excluded_tag(r.get("tags"))
    ]

    return {
        r["skyslopefileid"]: r
        for r in filtered
        if r.get("skyslopefileid") is not None
    }


# ---------------- MAIN ENGINE ----------------

COMPARE_VALUES_FIELDS = {
    "sale_price",
    "close_date",
    "listing_price",
    "contract_date",
    "listingguid",
    "gross_commission"
}

BUYER_SELLER_NAME_FIELD = {
    "buyer_name",
    "seller_name"
}

BUYING_AGENT_NAME_FIELD = {
    "buying_agent_name",
    "buying_agent"
}

STATUS_FIELD = {
    "status"
}


def run_field(field_name: str):

    if field_name not in FIELD_MAP and field_name != "status":
        return {"error": "Invalid field"}

    sales, be_data = load_data()

    # Skyslope lookup: saleguid keyed by BE file id
    sale_lookup = {s["saleguid"]: s for s in sales}

    results = []

    for b in be_data:

        # 1. filter BE rows
        tags = (b.get("tags") or "").lower()
        if "complete" in tags or "revoked" in tags:
            continue

        be_file_id = b.get("skyslopefileid")

        # 2. match Skyslope record
        s = sale_lookup.get(be_file_id)

        # 3. IMPORTANT: only real Skyslope match gets saleguid
        skyslope_saleguid = s.get("saleguid") if s else None

        transaction_id = b.get("transaction_identifier_transactionid")
        property_address = b.get("property_address")

        # ---------------- STATUS ----------------
        if field_name == "status":

            skyslope_status = normalize_value(s.get("status")) if s else None
            be_status_list = extract_be_status(b.get("tags"))
            be_status = ", ".join(be_status_list)

            if not s:
                match_result = "no_skyslope_record"
            else:
                match_result = "match" if skyslope_status == "Pending" else "mismatch"

            results.append({
                "saleguid": skyslope_saleguid,
                "transactionid": transaction_id,
                "propertyaddress": property_address,
                "skyslope_status": skyslope_status,
                "be_status": be_status,
                "match_result": match_result
            })

            continue

        # ---------------- GENERIC FIELDS ----------------
        config = FIELD_MAP[field_name]

        be_val = normalize_value(b.get(config["be"]))
        ss_val = normalize_value(s.get(config["ss"])) if s else None

        if not s:
            result = "no_skyslope_record"
        else:
            if field_name in COMPARE_VALUES_FIELDS:
                result = compare_values(be_val, ss_val)
            elif field_name in BUYER_SELLER_NAME_FIELD:
                result = compare_names(be_val, ss_val)
            elif field_name in BUYING_AGENT_NAME_FIELD:
                result = compare_buying_agent(be_val, ss_val)
            else:
                result = ""

        results.append({
            "saleguid": skyslope_saleguid,   # ONLY real match or None
            "transactionId": transaction_id,
            "propertyaddress": property_address,

            f"be_{field_name}": be_val,
            f"skyslope_{field_name}": ss_val,

            "match_result": result
        })

    return results


#brokerage engine
import json

WORKFLOW_STATUSES = [
    "Approved for Processing",
    "Approved for Commission",
    "Distribution Sent to Title",
    "Commission Verified",
]

def extract_brokerage_status(tags):

    if not tags:
        return ["Pending"]

    # normalize tags
    if isinstance(tags, list):
        tag_list = tags
    else:
        try:
            tag_list = json.loads(tags)
            if not isinstance(tag_list, list):
                tag_list = [str(tag_list)]
        except:
            tag_list = [t.strip() for t in str(tags).split(",")]

    found = set()

    has_terminal = False  # Complete or Revoked flag

    for tag in tag_list:
        t = str(tag).lower()

        # terminal statuses
        if "complete" in t:
            found.add("Complete")
            has_terminal = True

        if "revoked" in t:
            found.add("Revoked")
            has_terminal = True

        # workflow statuses
        for ws in WORKFLOW_STATUSES:
            if ws.lower() in t:
                found.add(ws)

    # if NO complete/revoked → ONLY Pending (ignore workflow entirely)
    if not has_terminal:
        return ["Pending"]

    return list(found)

def run_brokerage_engine():

    _, be_data = load_data()

    results = []

    for b in be_data:

        status_list = extract_brokerage_status(b.get("tags"))
        be_status = ", ".join(status_list)

        results.append({
            "transactionid": b.get("transaction_identifier_transactionid"),
            "property_address": b.get("property_address"),
            "buying_agent_name": b.get("buying_agent_name"),
            "sale_price": b.get("sale_price"),
            "contract_date": b.get("contract_date"),
            "close_date": b.get("closed_date"),
            "transaction_specialist": b.get("transaction_specialist"),
            "status": be_status,
            "skyslopefileid": b.get("skyslopefileid")
        })

    return results

#skyslope api
def get_skyslope_data():

    sales, _ = load_data()   # only use sales data

    results = []

    for s in sales:

        results.append({
            "saleguid": s.get("saleguid"),
            "contract_date": s.get("contractacceptancedate"),
            "propertyaddress": s.get("propertyaddress"),
            "close_date": s.get("escrowclosingdate"),
            "buyer_name": s.get("buyer_full_name"),
            "buyer_agent_name": s.get("agent_full_name"),
            "status": s.get("status"),
            "reviewer": s.get("reviewer_full_name")
        })

    return results