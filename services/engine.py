from .loaders import load_data
from .field_registry import FIELD_MAP
from .comparison import compare_values, compare_names, compare_buying_agent, normalize_value


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


def run_field(field_name: str):

    if field_name not in FIELD_MAP:
        return {"error": "Invalid field"}

    sales, be_data = load_data()
    be_lookup = build_lookup(be_data)

    config = FIELD_MAP[field_name]

    results = []

    for s in sales:
        sale_id = s["saleguid"]

        if sale_id not in be_lookup:
            continue

        b = be_lookup[sale_id]

        be_val = normalize_value(b.get(config["be"]))
        ss_val = normalize_value(s.get(config["ss"]))

        # default = blank
        result = ""

        if field_name in COMPARE_VALUES_FIELDS:
            result = compare_values(be_val, ss_val)
        elif field_name in BUYER_SELLER_NAME_FIELD:
            result = compare_names(be_val, ss_val)
        elif field_name in BUYING_AGENT_NAME_FIELD:
            result = compare_buying_agent(be_val, ss_val)

        results.append({
            "saleguid": sale_id,
            "transactionId": b.get("transaction_identifier_transactionid"),
            "propertyaddress": b.get("property_address"),

            f"be_{field_name}": be_val,
            f"skyslope_{field_name}": ss_val,

            "match_result": result
        })

    return results