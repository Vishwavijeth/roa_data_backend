from typing import Optional
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation

INSERT_SQL = """
INSERT INTO brokerage_engine (
    transaction_identifier_transactionid,
    transaction_identifier_transactionguid,
    listingguid,
    property_address,
    address_line1,
    address_line2,
    city,
    state,
    zip,
    property_type,
    property_subtype,
    listing_price,
    sale_price,
    contract_date,
    mls_number,
    date_created,
    seller_name,
    seller_email,
    buyer_name,
    buyer_email,
    outside_brokerage_name,
    outside_brokerage_agent,
    outside_brokerage_agent_email,
    listing_office,
    listing_agent_identifier,
    listing_agent_name,
    listing_agent_email,
    buying_office,
    buying_agent_identifier,
    buying_agent_name,
    buying_agent_email,
    listing_side_gross_commission,
    listing_side_agent_net,
    listing_side_brokerage_net,
    buying_side_gross_commission,
    buying_side_agent_net,
    buying_side_brokerage_net,
    total_gross_commission,
    total_agent_net,
    total_brokerage_net,
    da_title_company,
    da_closer_name,
    da_mailing_address,
    da_phone,
    da_email,
    mi_mortgage_company,
    mi_lender_name,
    mi_mailing_address,
    mi_phone,
    mi_email,
    referrals,
    tags,
    cancel_date,
    closed_date,
    finalized_date,
    skyslopefileid,
    transaction_specialist
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (transaction_identifier_transactionid) DO UPDATE SET
    transaction_identifier_transactionguid = EXCLUDED.transaction_identifier_transactionguid,
    listingguid                            = EXCLUDED.listingguid,
    property_address                       = EXCLUDED.property_address,
    address_line1                          = EXCLUDED.address_line1,
    address_line2                          = EXCLUDED.address_line2,
    city                                   = EXCLUDED.city,
    state                                  = EXCLUDED.state,
    zip                                    = EXCLUDED.zip,
    property_type                          = EXCLUDED.property_type,
    property_subtype                       = EXCLUDED.property_subtype,
    listing_price                          = EXCLUDED.listing_price,
    sale_price                             = EXCLUDED.sale_price,
    contract_date                          = EXCLUDED.contract_date,
    mls_number                             = EXCLUDED.mls_number,
    date_created                           = EXCLUDED.date_created,
    seller_name                            = EXCLUDED.seller_name,
    seller_email                           = EXCLUDED.seller_email,
    buyer_name                             = EXCLUDED.buyer_name,
    buyer_email                            = EXCLUDED.buyer_email,
    outside_brokerage_name                 = EXCLUDED.outside_brokerage_name,
    outside_brokerage_agent                = EXCLUDED.outside_brokerage_agent,
    outside_brokerage_agent_email          = EXCLUDED.outside_brokerage_agent_email,
    listing_office                         = EXCLUDED.listing_office,
    listing_agent_identifier               = EXCLUDED.listing_agent_identifier,
    listing_agent_name                     = EXCLUDED.listing_agent_name,
    listing_agent_email                    = EXCLUDED.listing_agent_email,
    buying_office                          = EXCLUDED.buying_office,
    buying_agent_identifier                = EXCLUDED.buying_agent_identifier,
    buying_agent_name                      = EXCLUDED.buying_agent_name,
    buying_agent_email                     = EXCLUDED.buying_agent_email,
    listing_side_gross_commission          = EXCLUDED.listing_side_gross_commission,
    listing_side_agent_net                 = EXCLUDED.listing_side_agent_net,
    listing_side_brokerage_net             = EXCLUDED.listing_side_brokerage_net,
    buying_side_gross_commission           = EXCLUDED.buying_side_gross_commission,
    buying_side_agent_net                  = EXCLUDED.buying_side_agent_net,
    buying_side_brokerage_net              = EXCLUDED.buying_side_brokerage_net,
    total_gross_commission                 = EXCLUDED.total_gross_commission,
    total_agent_net                        = EXCLUDED.total_agent_net,
    total_brokerage_net                    = EXCLUDED.total_brokerage_net,
    da_title_company                       = EXCLUDED.da_title_company,
    da_closer_name                         = EXCLUDED.da_closer_name,
    da_mailing_address                     = EXCLUDED.da_mailing_address,
    da_phone                               = EXCLUDED.da_phone,
    da_email                               = EXCLUDED.da_email,
    mi_mortgage_company                    = EXCLUDED.mi_mortgage_company,
    mi_lender_name                         = EXCLUDED.mi_lender_name,
    mi_mailing_address                     = EXCLUDED.mi_mailing_address,
    mi_phone                               = EXCLUDED.mi_phone,
    mi_email                               = EXCLUDED.mi_email,
    referrals                              = EXCLUDED.referrals,
    tags                                   = EXCLUDED.tags,
    cancel_date                            = EXCLUDED.cancel_date,
    closed_date                            = EXCLUDED.closed_date,
    finalized_date                         = EXCLUDED.finalized_date,
    skyslopefileid                         = EXCLUDED.skyslopefileid,
    transaction_specialist                 = EXCLUDED.transaction_specialist
"""

# ──────────────────────────────────────────────
# Field classification sets  (same as brokerage_engine.py)
# ──────────────────────────────────────────────

UUID_FIELDS = {
    "Transaction_Identifier_TransactionId",
    "Transaction_Identifier_TransactionGuid",
    "ListingGuid",
    "SkySlopeFileID",
}

# All normal date fields use DD-MM-YYYY
DATE_FIELDS = {
    "Contract_Date",
    "Cancel_Date",
    "Closed_Date",
    "Finalized_Date",
}

NUMERIC_FIELDS = {
    "Listing_Price",
    "Sale_Price",
    "Listing_Side_Gross_Commission",
    "Listing_Side_Agent_Net",
    "Listing_Side_Brokerage_Net",
    "Buying_Side_Gross_Commission",
    "Buying_Side_Agent_Net",
    "Buying_Side_Brokerage_Net",
    "Total_Gross_Commission",
    "Total_Agent_Net",
    "Total_Brokerage_Net",
}

# ──────────────────────────────────────────────
# Transform helpers
# ──────────────────────────────────────────────

def clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value if value else None


def parse_uuid(value: Optional[str]) -> Optional[str]:
    value = clean_text(value)
    if not value:
        return None
    return str(uuid.UUID(value))


# Try formats in priority order:
#   1. YYYY-MM-DD  — live API (ISO 8601)   e.g. 2024-08-03
#   2. DD-MM-YYYY  — legacy local CSV      e.g. 03-08-2024
#   3. MM-DD-YYYY  — legacy Date_Created   e.g. 08-03-2024
_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%m-%d-%Y")


def parse_date(value: Optional[str]):
    """Try each known date format in order; return a date object or None."""
    value = clean_text(value)
    if not value:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(
        f"Date '{value}' does not match any known format: {_DATE_FORMATS}"
    )


def parse_numeric(value: Optional[str]) -> Optional[Decimal]:
    value = clean_text(value)
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def transform(field_name: str, value: Optional[str]):
    """Apply the correct type conversion for a given CSV field."""
    if field_name in UUID_FIELDS:
        return parse_uuid(value)

    # All date fields — parse_date() handles YYYY-MM-DD, DD-MM-YYYY, MM-DD-YYYY
    if field_name in DATE_FIELDS or field_name == "Date_Created":
        return parse_date(value)

    if field_name in NUMERIC_FIELDS:
        return parse_numeric(value)

    return clean_text(value)


def build_row_values(row: dict) -> list:
    """Map a CSV DictReader row to the ordered list expected by INSERT_SQL."""
    return [
        transform("Transaction_Identifier_TransactionId",   row.get("Transaction_Identifier_TransactionId", "")),
        transform("Transaction_Identifier_TransactionGuid", row.get("Transaction_Identifier_TransactionGuid", "")),
        transform("ListingGuid",                            row.get("ListingGuid", "")),
        transform("Property_Address",                       row.get("Property_Address", "")),
        transform("Address_Line1",                          row.get("Address_Line1", "")),
        transform("Address_Line2",                          row.get("Address_Line2", "")),
        transform("City",                                   row.get("City", "")),
        transform("State",                                  row.get("State", "")),
        transform("Zip",                                    row.get("Zip", "")),
        transform("Property_Type",                          row.get("Property_Type", "")),
        transform("Property_Subtype",                       row.get("Property_Subtype", "")),
        transform("Listing_Price",                          row.get("Listing_Price", "")),
        transform("Sale_Price",                             row.get("Sale_Price", "")),
        transform("Contract_Date",                          row.get("Contract_Date", "")),
        transform("MLS_Number",                             row.get("MLS_Number", "")),
        # Special column — MM-DD-YYYY
        transform("Date_Created",                           row.get("Date_Created", "")),
        transform("Seller_Name",                            row.get("Seller_Name", "")),
        transform("Seller_Email",                           row.get("Seller_Email", "")),
        transform("Buyer_Name",                             row.get("Buyer_Name", "")),
        transform("Buyer_Email",                            row.get("Buyer_Email", "")),
        transform("Outside_Brokerage_Name",                 row.get("Outside_Brokerage_Name", "")),
        transform("Outside_Brokerage_Agent",                row.get("Outside_Brokerage_Agent", "")),
        transform("Outside_Brokerage_Agent_Email",          row.get("Outside_Brokerage_Agent_Email", "")),
        transform("Listing_Office",                         row.get("Listing_Office", "")),
        transform("Listing_Agent_Identifier",               row.get("Listing_Agent_Identifier", "")),
        transform("Listing_Agent_Name",                     row.get("Listing_Agent_Name", "")),
        transform("Listing_Agent_Email",                    row.get("Listing_Agent_Email", "")),
        transform("Buying_Office",                          row.get("Buying_Office", "")),
        transform("Buying_Agent_Identifier",                row.get("Buying_Agent_Identifier", "")),
        transform("Buying_Agent_Name",                      row.get("Buying_Agent_Name", "")),
        transform("Buying_Agent_Email",                     row.get("Buying_Agent_Email", "")),
        transform("Listing_Side_Gross_Commission",          row.get("Listing_Side_Gross_Commission", "")),
        transform("Listing_Side_Agent_Net",                 row.get("Listing_Side_Agent_Net", "")),
        transform("Listing_Side_Brokerage_Net",             row.get("Listing_Side_Brokerage_Net", "")),
        transform("Buying_Side_Gross_Commission",           row.get("Buying_Side_Gross_Commission", "")),
        transform("Buying_Side_Agent_Net",                  row.get("Buying_Side_Agent_Net", "")),
        transform("Buying_Side_Brokerage_Net",              row.get("Buying_Side_Brokerage_Net", "")),
        transform("Total_Gross_Commission",                 row.get("Total_Gross_Commission", "")),
        transform("Total_Agent_Net",                        row.get("Total_Agent_Net", "")),
        transform("Total_Brokerage_Net",                    row.get("Total_Brokerage_Net", "")),
        transform("DA_Title_Company",                       row.get("DA_Title_Company", "")),
        transform("DA_Closer_Name",                         row.get("DA_Closer_Name", "")),
        transform("DA_Mailing_Address",                     row.get("DA_Mailing_Address", "")),
        transform("DA_Phone",                               row.get("DA_Phone", "")),
        transform("DA_Email",                               row.get("DA_Email", "")),
        transform("MI_Mortgage_Company",                    row.get("MI_Mortgage_Company", "")),
        transform("MI_Lender_Name",                         row.get("MI_Lender_Name", "")),
        transform("MI_Mailing_Address",                     row.get("MI_Mailing_Address", "")),
        transform("MI_Phone",                               row.get("MI_Phone", "")),
        transform("MI_Email",                               row.get("MI_Email", "")),
        transform("Referrals",                              row.get("Referrals", "")),
        transform("Tags",                                   row.get("Tags", "")),
        transform("Cancel_Date",                            row.get("Cancel_Date", "")),
        transform("Closed_Date",                            row.get("Closed_Date", "")),
        transform("Finalized_Date",                         row.get("Finalized_Date", "")),
        transform("SkySlopeFileID",                         row.get("SkySlopeFileID", "")),
        # CSV header has a space: "Transaction Specialist"
        transform("Transaction_Specialist",                 row.get("Transaction Specialist", "")),
    ]