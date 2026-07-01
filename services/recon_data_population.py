def get_skyslope_gross_commission(row: dict):
    office_gci = row.get("officegrosscommissiononsale")
    admin_brokeage = row.get("adminbrokeagecomp")

    if office_gci is None and admin_brokeage is None:
        return None
    if office_gci is None:
        return admin_brokeage
    if admin_brokeage is None:
        return office_gci
    return office_gci + admin_brokeage


def normalize_text(value):
    if value is None:
        return None

    value = str(value).replace("\xa0", " ").strip()
    if not value:
        return None

    value = " ".join(value.split())
    return value.lower()


def normalize_buying_agent(value):
    if value is None:
        return None

    value = str(value).replace("[T]", "").replace("\xa0", " ").strip()
    if not value:
        return None

    value = " ".join(value.split())
    return value.lower()


def expand_ampersand_name(name):
    name = normalize_text(name)
    if not name:
        return []

    if " & " not in name:
        return [name]

    parts = [part.strip() for part in name.split("&") if part.strip()]
    if len(parts) != 2:
        return [name]

    left_words = parts[0].split()
    right_words = parts[1].split()

    if len(left_words) >= 2 and len(right_words) >= 2:
        return [" ".join(left_words), " ".join(right_words)]

    if len(left_words) == 1 and len(right_words) >= 2:
        last_name = right_words[-1]
        return [f"{left_words[0]} {last_name}", " ".join(right_words)]

    if len(left_words) >= 2 and len(right_words) == 1:
        last_name = left_words[-1]
        return [" ".join(left_words), f"{right_words[0]} {last_name}"]

    return [name]


def split_names(value):
    value = normalize_text(value)
    if not value:
        return set()

    result = set()
    for chunk in value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        for item in expand_ampersand_name(chunk):
            if item:
                result.add(item)
    return result


def split_buying_agent_names(value):
    value = normalize_buying_agent(value)
    if not value:
        return set()

    result = set()
    for chunk in value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue

        chunk = normalize_buying_agent(chunk)
        if not chunk:
            continue

        if " & " not in chunk:
            result.add(chunk)
            continue

        parts = [part.strip() for part in chunk.split("&") if part.strip()]
        if len(parts) != 2:
            result.add(chunk)
            continue

        left_words = parts[0].split()
        right_words = parts[1].split()

        if len(left_words) >= 2 and len(right_words) >= 2:
            result.add(" ".join(left_words))
            result.add(" ".join(right_words))
        elif len(left_words) == 1 and len(right_words) >= 2:
            last_name = right_words[-1]
            result.add(f"{left_words[0]} {last_name}")
            result.add(" ".join(right_words))
        elif len(left_words) >= 2 and len(right_words) == 1:
            last_name = left_words[-1]
            result.add(" ".join(left_words))
            result.add(f"{right_words[0]} {last_name}")
        else:
            result.add(chunk)

    return result


def compare_names(value1, value2):
    if value1 is None and value2 is None:
        return None

    names1 = split_names(value1)
    names2 = split_names(value2)

    if not names1 or not names2:
        return "mismatch"

    return "match" if names1.intersection(names2) else "mismatch"


def compare_buying_agent(value1, value2):
    if value1 is None and value2 is None:
        return None

    names1 = split_buying_agent_names(value1)
    names2 = split_buying_agent_names(value2)

    if not names1 or not names2:
        return "mismatch"

    return "match" if names1.intersection(names2) else "mismatch"


def compare_dates(value1, value2):
    if value1 is None and value2 is None:
        return None
    if value1 is None or value2 is None:
        return "mismatch"
    return "match" if value1 == value2 else "mismatch"


def compare_numbers(value1, value2, treat_zero_as_mismatch=False):
    if value1 is None and value2 is None:
        return None
    if value1 is None or value2 is None:
        return "mismatch"

    n1 = float(value1)
    n2 = float(value2)

    if treat_zero_as_mismatch and (n1 == 0 or n2 == 0):
        return "mismatch"

    return "match" if round(n1, 2) == round(n2, 2) else "mismatch"


def compare_status(be_status, skyslope_status, is_cancelled=False):
    if be_status is None and skyslope_status is None:
        return None

    if is_cancelled:
        return "match"

    if be_status is None or skyslope_status is None:
        return "mismatch"

    be = be_status.lower()
    ss = skyslope_status.lower()

    if be == "closed" and ss in ["archived", "closed"]:
        return "match"

    if be == ss:
        return "match"

    if be == "pending" and ss == "expired":
        return "mismatch"

    return "mismatch"


def get_contract_date_match(row: dict):
    saleguid = row.get("saleguid")
    be_status = row.get("be_status")
    skyslope_status = row.get("skyslope_status")

    is_cancelled = (
        be_status
        and be_status.lower() == "cancelled"
        and skyslope_status
        and skyslope_status.lower() in ["canceled/pend", "canceled/app"]
    )

    be_contract = row.get("be_contract_date")
    ss_contract = row.get("skyslope_contract_date")

    if saleguid is None:
        return "no_skyslope_record"
    if is_cancelled:
        return None
    return compare_dates(be_contract, ss_contract)


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
        gci_result = compare_numbers(be_gci, ss_gci, treat_zero_as_mismatch=True)

    be_close = row.get("be_close_date")
    ss_close = row.get("skyslope_close_date")

    if saleguid is None:
        close_result = "no_skyslope_record"
    elif is_cancelled:
        close_result = None
    else:
        close_result = compare_dates(be_close, ss_close)

    if saleguid is None:
        status_result = "no_skyslope_record"
    else:
        status_result = compare_status(be_status, skyslope_status, is_cancelled=is_cancelled)

    be_sale = row.get("be_sale_price")
    ss_sale = row.get("skyslope_sale_price")

    if saleguid is None:
        sale_price_result = "no_skyslope_record"
    elif is_cancelled:
        sale_price_result = None
    else:
        sale_price_result = compare_numbers(be_sale, ss_sale)

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
    else:
        be_list = row.get("be_listing_price")
        ss_list = row.get("skyslope_listing_price")

        if saleguid is None:
            listing_price_result = "no_skyslope_record"
        elif is_cancelled:
            listing_price_result = None
        else:
            listing_price_result = compare_numbers(be_list, ss_list, treat_zero_as_mismatch=True)

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