from services.comparison import compare_names, compare_buying_agent

def compare_names_fast(be_val, ss_val):
    if be_val is None or ss_val is None:
        return "null"

    be_clean = be_val.strip().lower()
    ss_clean = ss_val.strip().lower()

    if be_clean == ss_clean:
        return "match"

    if be_clean == "" or ss_clean == "":
        return "null"

    return compare_names(be_val, ss_val)


def compare_buying_agent_fast(be_val, ss_val):
    if be_val is None or ss_val is None:
        return "null"

    be_clean = be_val.strip().lower()
    ss_clean = ss_val.strip().lower()

    if be_clean == ss_clean:
        return "match"

    if be_clean == "" or ss_clean == "":
        return "null"

    return compare_buying_agent(be_val, ss_val)

def evaluate_row(row):
    source_table = row.get("source_table")
    saleguid = row.get("saleguid")
    be_status = row.get("be_status")
    skyslope_status = row.get("skyslope_status")
    tags = (row.get("tags") or "").lower()

    is_cancelled = False
    if be_status and be_status.lower() == "cancelled":
        if skyslope_status and skyslope_status.lower() in ["canceled/pend", "canceled/app"]:
            is_cancelled = True

    be_gci = row.get("be_gross_commission")
    office_gci = row.get("officegrosscommissiononsale")
    listing_comm = row.get("listingcommissionamount")
    sale_comm = row.get("salecommissionamount")

    if "listingside" in tags and "sellingside" in tags:
        ss_gci = office_gci
    elif "listingside" in tags:
        ss_gci = listing_comm if listing_comm is not None else office_gci
    elif "sellingside" in tags:
        ss_gci = sale_comm if sale_comm is not None else office_gci
    else:
        ss_gci = sale_comm if sale_comm is not None else office_gci

    gci_result = None
    if saleguid is None:
        gci_result = "no_skyslope_record"
    elif is_cancelled:
        gci_result = None
    elif be_gci is None or ss_gci is None or float(be_gci) == 0 or float(ss_gci) == 0:
        gci_result = None
    else:
        gci_result = "match" if round(float(be_gci), 2) == round(float(ss_gci), 2) else "mismatch"

    be_gci_val = float(be_gci) if be_gci is not None else None
    ss_gci_val = float(ss_gci) if ss_gci is not None else None

    be_close = row.get("be_close_date")
    ss_close = row.get("skyslope_close_date")
    close_result = None
    if saleguid is None:
        close_result = "no_skyslope_record"
    elif is_cancelled:
        close_result = None
    elif be_close is None or ss_close is None:
        close_result = None
    else:
        close_result = "match" if be_close == ss_close else "mismatch"

    be_close_str = be_close.isoformat() if hasattr(be_close, "isoformat") else be_close
    ss_close_str = ss_close.isoformat() if hasattr(ss_close, "isoformat") else ss_close

    status_result = "mismatch"
    if saleguid is None:
        status_result = "no_skyslope_record"
    elif is_cancelled:
        status_result = "match"
    elif be_status and be_status.lower() == "closed" and skyslope_status and skyslope_status.lower() in ["archived", "closed"]:
        status_result = "match"
    elif be_status and skyslope_status and be_status.lower() == skyslope_status.lower():
        status_result = "match"
    elif be_status and be_status.lower() == "pending" and skyslope_status and skyslope_status.lower() == "expired":
        status_result = None
    elif be_status is None or skyslope_status is None:
        status_result = "mismatch"
    else:
        status_result = "mismatch"

    be_sale = row.get("be_sale_price")
    ss_sale = row.get("skyslope_sale_price")
    sale_price_result = None
    if saleguid is None:
        sale_price_result = "no_skyslope_record"
    elif is_cancelled:
        sale_price_result = None
    elif be_sale is None or ss_sale is None:
        sale_price_result = None
    else:
        sale_price_result = "match" if float(be_sale) == float(ss_sale) else "mismatch"

    be_sale_val = float(be_sale) if be_sale is not None else None
    ss_sale_val = float(ss_sale) if ss_sale is not None else None

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
        listing_price_result = None
        if saleguid is None:
            listing_price_result = "no_skyslope_record"
        elif is_cancelled:
            listing_price_result = None
        elif be_list is None or ss_list is None or float(be_list) == 0 or float(ss_list) == 0:
            listing_price_result = None
        else:
            listing_price_result = "match" if float(be_list) == float(ss_list) else "mismatch"

        be_list_val = float(be_list) if be_list is not None else None
        ss_list_val = float(ss_list) if ss_list is not None else None

        be_buyer = row.get("be_buyer_name")
        ss_buyer = row.get("skyslope_buyer_name")
        if saleguid is None:
            buyer_name_result = "no_skyslope_record"
        elif is_cancelled:
            buyer_name_result = None
        else:
            buyer_name_result = compare_names_fast(be_buyer, ss_buyer)

        be_seller = row.get("be_seller_name")
        ss_seller = row.get("skyslope_seller_name")
        if saleguid is None:
            seller_name_result = "no_skyslope_record"
        elif is_cancelled:
            seller_name_result = None
        else:
            seller_name_result = compare_names_fast(be_seller, ss_seller)

        be_agent = row.get("be_buying_agent_name")
        ss_agent = row.get("skyslope_buying_agent_name")
        if saleguid is None:
            buying_agent_result = "no_skyslope_record"
        elif is_cancelled:
            buying_agent_result = None
        else:
            buying_agent_result = compare_buying_agent_fast(be_agent, ss_agent)

        be_title = row.get("be_title_company")
        ss_title = row.get("skyslope_title_company")
        if saleguid is None:
            title_company_result = "no_skyslope_record"
        elif is_cancelled:
            title_company_result = None
        else:
            title_company_result = compare_names_fast(be_title, ss_title)

    return {
        "gross_commission": {
            "be_value": be_gci_val,
            "skyslope_value": ss_gci_val,
            "match_result": gci_result,
        },
        "close_date": {
            "be_value": be_close_str,
            "skyslope_value": ss_close_str,
            "match_result": close_result,
        },
        "status": {
            "be_value": be_status,
            "skyslope_value": skyslope_status,
            "match_result": status_result,
        },
        "sale_price": {
            "be_value": be_sale_val,
            "skyslope_value": ss_sale_val,
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