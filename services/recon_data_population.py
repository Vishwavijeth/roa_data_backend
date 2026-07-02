import re

def get_skyslope_gross_commission(row: dict):
    office_gci = row.get("officegrosscommissiononsale")
    admin_brokeage = row.get("adminbrokeragecomp")

    if office_gci is None and admin_brokeage is None:
        return None
    if office_gci is None:
        return admin_brokeage
    if admin_brokeage is None:
        return office_gci
    return office_gci + admin_brokeage


NULL_NAME_STRINGS = {'na', 'n/a', 'na na', '-', '--', '---'}

# precompiled patterns
INVISIBLE_CHARS_RE = re.compile(r'[\u200B-\u200D\uFEFF]')
WHITESPACE_RE = re.compile(r'\s+')
NAME_PATTERN_RE = re.compile(r'[a-z]+(?:\s+[a-z]+)+')  # full-name pattern


def is_null_name(val):
    """Return True if the value represents an invalid/NA name."""
    if val is None:
        return True

    val = str(val).strip().lower()
    if val in NULL_NAME_STRINGS:
        return True

    tokens = WHITESPACE_RE.sub(' ', val).split()
    return bool(tokens) and all(t in NULL_NAME_STRINGS for t in tokens)


def canonical_name(val):
    """Normalize name string: remove invisible chars, normalize spaces, lowercase."""
    if val is None:
        return None

    val = str(val)
    val = INVISIBLE_CHARS_RE.sub('', val)
    val = WHITESPACE_RE.sub(' ', val)
    val = val.replace('\u00A0', ' ')
    return val.strip().lower()


def split_names(val):
    if val is None:
        return None

    raw = str(val).strip()
    if not raw:
        return None

    parts = [p.strip() for p in raw.split(',')]
    cleaned = []

    for p in parts:
        if is_null_name(p):
            return None  # strict rule: any NA invalidates
        cleaned.append(canonical_name(p))

    return cleaned if cleaned else None


def compare_names(sale_name, be_name):
    sale_list = split_names(sale_name)
    be_list = split_names(be_name)

    if sale_list is None or be_list is None:
        return 'null'

    sale_set = set(sale_list)
    be_set = set(be_list)

    return 'match' if sale_set & be_set else 'mismatch'


def compare_buying_agent(be_value, skyslope_value):
    be_clean = canonical_name(be_value)
    skyslope_clean = canonical_name(skyslope_value)

    if be_clean is None or skyslope_clean is None:
        return 'null'

    be_names = NAME_PATTERN_RE.findall(be_clean)
    skyslope_names = NAME_PATTERN_RE.findall(skyslope_clean)

    if not be_names or not skyslope_names:
        return 'null'

    for sk_name in skyslope_names:
        if sk_name in be_clean:
            return 'match'

    return 'mismatch'


def compare_close_date(value1, value2):
    if value1 is None and value2 is None:
        return None
    if value1 is None or value2 is None:
        return "mismatch"
    return "match" if value1 == value2 else "mismatch"

def compare_listing_price(be_listing_price, ss_listing_price):
    if be_listing_price is None or ss_listing_price is None:
        return None

    be_val = float(be_listing_price)
    ss_val = float(ss_listing_price)

    if be_val == 0 or ss_val == 0:
        return None

    return "match" if round(be_val, 2) == round(ss_val, 2) else "mismatch"

def compare_contract_date(be_contract_date, ss_contract_date):
    if be_contract_date is None and ss_contract_date is None:
        return "match"

    if be_contract_date is None or ss_contract_date is None:
        return "mismatch"

    be_val = (
        be_contract_date.isoformat()
        if hasattr(be_contract_date, "isoformat")
        else str(be_contract_date)
    )
    ss_val = (
        ss_contract_date.isoformat()
        if hasattr(ss_contract_date, "isoformat")
        else str(ss_contract_date)
    )

    return "match" if be_val == ss_val else "mismatch"


def compare_gci_and_saleprice(value1, value2, treat_zero_as_mismatch=False):
    if value1 is None and value2 is None:
        return None
    if value1 is None or value2 is None:
        return "mismatch"

    n1 = float(value1)
    n2 = float(value2)

    if treat_zero_as_mismatch and (n1 == 0 or n2 == 0):
        return "mismatch"

    return "match" if round(n1, 2) == round(n2, 2) else "mismatch"


def compare_status(be_status, skyslope_status, saleguid):
    if saleguid is None:
        return "no_skyslope_record"

    be = (be_status or "").lower()
    ss = (skyslope_status or "").lower()

    if be == "cancelled" and ss in ["canceled/app", "canceled/pend"]:
        return "match"

    if be == "closed" and ss in ["archived", "closed"]:
        return "match"

    if be == ss:
        return "match"

    if be == "pending" and ss == "expired":
        return None

    return "mismatch"


def evaluate_row(row):
    source_table = row.get("source_table")
    saleguid = row.get("saleguid")
    be_status = row.get("be_status")
    skyslope_status = row.get("skyslope_status")


    is_cancelled = (
        be_status
        and be_status.lower() == "cancelled"
        and skyslope_status
        and skyslope_status.lower() in ["canceled/pend", "canceled/app"]
    )

    be_gci = row.get("be_gross_commission")
    ss_gci = get_skyslope_gross_commission(row)

    if saleguid is None:
        gci_result = "no_skyslope_record"
    elif is_cancelled:
        gci_result = None
    else:
        gci_result = compare_gci_and_saleprice(be_gci, ss_gci, treat_zero_as_mismatch=True)

    be_close = row.get("be_close_date")
    ss_close = row.get("skyslope_close_date")

    if saleguid is None:
        close_result = "no_skyslope_record"
    elif is_cancelled:
        close_result = None
    else:
        close_result = compare_close_date(be_close, ss_close)

    status_result = compare_status(be_status, skyslope_status, saleguid)

    be_sale = row.get("be_sale_price")
    ss_sale = row.get("skyslope_sale_price")

    if saleguid is None:
        sale_price_result = "no_skyslope_record"
    elif is_cancelled:
        sale_price_result = None
    else:
        sale_price_result = compare_gci_and_saleprice(be_sale, ss_sale)

    if source_table == "otherincome_transactions":
        listing_price_result = None
        buyer_name_result = None
        seller_name_result = None
        buying_agent_result = None
        title_company_result = None

        be_list_val = None
        ss_list_val = None
        be_buyer = None
        ss_buyer = None
        be_seller = None
        ss_seller = None
        be_agent = None
        ss_agent = None
        be_title = None
        ss_title = None
        be_contract_date = None
        ss_contract_date = None
    else:
        be_list = row.get("be_listing_price")
        ss_list = row.get("skyslope_listing_price")

        if saleguid is None:
            listing_price_result = "no_skyslope_record"
        elif is_cancelled:
            listing_price_result = None
        else:
            listing_price_result = compare_listing_price(be_list, ss_list)

        be_list_val = float(be_list) if be_list is not None else None
        ss_list_val = float(ss_list) if ss_list is not None else None

        be_buyer = row.get("be_buyer_name")
        ss_buyer = row.get("skyslope_buyer_name")
        if saleguid is None:
            buyer_name_result = "no_skyslope_record"
        elif is_cancelled:
            buyer_name_result = None
        else:
            buyer_name_result = compare_names(be_buyer, ss_buyer)

        be_seller = row.get("be_seller_name")
        ss_seller = row.get("skyslope_seller_name")
        if saleguid is None:
            seller_name_result = "no_skyslope_record"
        elif is_cancelled:
            seller_name_result = None
        else:
            seller_name_result = compare_names(be_seller, ss_seller)

        be_agent = row.get("be_buying_agent_name")
        ss_agent = row.get("skyslope_buying_agent_name")
        if saleguid is None:
            buying_agent_result = "no_skyslope_record"
        elif is_cancelled:
            buying_agent_result = None
        else:
            buying_agent_result = compare_buying_agent(be_agent, ss_agent)

        be_title = row.get("be_title_company")
        ss_title = row.get("skyslope_title_company")
        if saleguid is None:
            title_company_result = "no_skyslope_record"
        elif is_cancelled:
            title_company_result = None
        else:
            title_company_result = compare_names(be_title, ss_title)

        be_contract_date = row.get("be_contract_date")
        ss_contract_date = row.get("skyslope_contract_acceptance_date")
        if saleguid is None:
            contract_date_result = "no_skyslope_record"
        elif is_cancelled:
            contract_date_result = None
        else:
            contract_date_result = compare_contract_date(
                be_contract_date,
                ss_contract_date,
            )

    return {
        "gross_commission": {
            "be_value": float(be_gci) if be_gci is not None else None,
            "skyslope_value": float(ss_gci) if ss_gci is not None else None,
            "match_result": gci_result,
        },
        "close_date": {
            "be_value": be_close.isoformat() if hasattr(be_close, "isoformat") else be_close,
            "skyslope_value": ss_close.isoformat() if hasattr(ss_close, "isoformat") else ss_close,
            "match_result": close_result,
        },
        "status": {
            "be_value": be_status,
            "skyslope_value": skyslope_status,
            "match_result": status_result,
        },
        "sale_price": {
            "be_value": float(be_sale) if be_sale is not None else None,
            "skyslope_value": float(ss_sale) if ss_sale is not None else None,
            "match_result": sale_price_result,
        },
        "listing_price": {
            "be_value": be_list_val,
            "skyslope_value": ss_list_val,
            "match_result": listing_price_result,
        },
        "contract_date": {
            "be_value": (
                be_contract_date.isoformat()
                if hasattr(be_contract_date, "isoformat")
                else be_contract_date
            ),
            "skyslope_value": (
                ss_contract_date.isoformat()
                if hasattr(ss_contract_date, "isoformat")
                else ss_contract_date
            ),
            "match_result": contract_date_result,
        },
        "buyer_name": {
            "be_value": be_buyer,
            "skyslope_value": ss_buyer,
            "match_result": buyer_name_result,
        },
        "seller_name": {
            "be_value": be_seller,
            "skyslope_value": ss_seller,
            "match_result": seller_name_result,
        },
        "buying_agent_name": {
            "be_value": be_agent,
            "skyslope_value": ss_agent,
            "match_result": buying_agent_result,
        },
        "title_company": {
            "be_value": be_title,
            "skyslope_value": ss_title,
            "match_result": title_company_result,
        },
    }