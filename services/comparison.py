from decimal import Decimal
import re

def normalize_value(val):
    if val is None or val == '' or val == 0:
        return None
    return val

def compare_values(sale_val, be_val):
    sale_val = normalize_value(sale_val)
    be_val = normalize_value(be_val)

    if sale_val is None or be_val is None:
        return 'null'

    if isinstance(sale_val, (int, float, Decimal)) and isinstance(be_val, (int, float, Decimal)):
        return 'match' if abs(float(sale_val) - float(be_val)) < 0.0001 else 'mismatch'

    return 'match' if str(sale_val) == str(be_val) else 'mismatch'

def normalize_value(val):
    if val is None:
        return None

    if isinstance(val, str):
        val = re.sub(r'[\u200B-\u200D\uFEFF]', '', val)
        val = val.replace('\u00A0', ' ')
        val = re.sub(r'\s+', ' ', val).strip()
        return val if val != '' else None

    return val


def split_names_for_compare(val):
    """
    Split raw value by comma.
    Only lowercase and strip each split piece for comparison.
    No null-name-string checks.
    """
    if val is None:
        return None

    raw = str(val).strip()
    if raw == '':
        return None

    parts = [p.strip().lower() for p in raw.split(',')]
    return parts if parts else None


def compare_names(sale_name, be_name):
    """
    Compare raw name data from both systems.
    - split both sides by comma
    - lowercase only
    - if any name matches -> match
    - otherwise mismatch
    """

    sale_list = split_names_for_compare(sale_name)
    be_list = split_names_for_compare(be_name)

    if sale_list is None or be_list is None:
        return 'mismatch'

    for sale_item in sale_list:
        for be_item in be_list:
            if sale_item == be_item:
                return 'match'

    return 'mismatch'


def compare_names_fast(be_val, ss_val):
    if be_val is None or ss_val is None:
        return "mismatch"

    be_clean = str(be_val).strip().lower()
    ss_clean = str(ss_val).strip().lower()

    if be_clean == "" or ss_clean == "":
        return "mismatch"

    if be_clean == ss_clean:
        return "match"

    return compare_names(be_val, ss_val)


def compare_buying_agent(be_value, skyslope_value):
    """
    Compare raw buying-agent data from both systems.
    - split both sides by comma
    - lowercase only
    - if any name matches -> match
    - otherwise mismatch
    """

    be_list = split_names_for_compare(be_value)
    skyslope_list = split_names_for_compare(skyslope_value)

    if be_list is None or skyslope_list is None:
        return 'mismatch'

    for be_item in be_list:
        for ss_item in skyslope_list:
            if be_item == ss_item:
                return 'match'

    return 'mismatch'


def compare_buying_agent_fast(be_val, ss_val):
    if be_val is None or ss_val is None:
        return "mismatch"

    be_clean = str(be_val).strip().lower()
    ss_clean = str(ss_val).strip().lower()

    if be_clean == "" or ss_clean == "":
        return "mismatch"

    if be_clean == ss_clean:
        return "match"

    return compare_buying_agent(be_val, ss_val)


def compare_listing_price(be_price, ss_price):
    """
    - If either side is None/null → return 'null' (indeterminate)
    - Otherwise compare numerically → return 'match' or 'mismatch'
    """
    if be_price is None or ss_price is None:
        return 'null'
    return 'match' if float(be_price) == float(ss_price) else 'mismatch'


def compare_transaction_specialist(be, ss):
    if be is None or ss is None:
        return "null"
    return "match" if ss.lower() in be.lower() else "mismatch"

# status comparison
STATUS_KEYWORDS = [
    "FellThrough",
    "Complete",
    "Revoked",
    "Open"
]


def normalize_status(value):
    if not value:
        return None

    v = value.lower()

    if "complete" in v:
        return "complete"
    if "revoked" in v:
        return "revoked"
    if "closed" in v:
        return "closed"
    if "fellthrough" in v or "fell through" in v:
        return "fell_through"
    if "open" in v:
        return "open"
    if "pending" in v:
        return "pending"

    return v


def extract_be_status(tags):
    if not tags:
        return ["Pending"]

    tags_lower = tags.lower().replace(" ", "")

    be_status_list = []

    # priority statuses
    if "complete" in tags_lower:
        be_status_list.append("Complete")

    if "revoked" in tags_lower:
        be_status_list.append("Revoked")

    if "fellthrough" in tags_lower:
        be_status_list.append("FellThrough")

    # Open → adds Pending (ONLY case where Pending is added)
    if "open" in tags_lower:
        be_status_list.append("Open")
        be_status_list.append("Pending")

    # if nothing matched at all
    if not be_status_list:
        be_status_list.append("Pending")

    return list(dict.fromkeys(be_status_list))
