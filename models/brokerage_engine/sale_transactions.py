from sqlalchemy import Column, BigInteger, Text, Date, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from db import Base

class BESaleTransactions(Base):
    __tablename__ = "brokerage_engine"

    id = Column(BigInteger, autoincrement=True)

    transaction_identifier_transactionid = Column(
        UUID(as_uuid=True),
        primary_key=True
    )

    transaction_identifier_transactionguid = Column(
        UUID(as_uuid=True)
    )

    listingguid = Column(UUID(as_uuid=True))

    property_address = Column(Text)
    address_line1 = Column(Text)
    address_line2 = Column(Text)
    city = Column(Text)
    state = Column(Text)
    zip = Column(Text)

    property_type = Column(Text)
    property_subtype = Column(Text)

    listing_price = Column(Numeric)
    sale_price = Column(Numeric)

    contract_date = Column(Date)
    mls_number = Column(Text)
    date_created = Column(Date)

    seller_name = Column(Text)
    seller_email = Column(Text)

    buyer_name = Column(Text)
    buyer_email = Column(Text)

    outside_brokerage_name = Column(Text)
    outside_brokerage_agent = Column(Text)
    outside_brokerage_agent_email = Column(Text)

    listing_office = Column(Text)
    listing_agent_identifier = Column(Text)
    listing_agent_name = Column(Text)
    listing_agent_email = Column(Text)

    buying_office = Column(Text)
    buying_agent_identifier = Column(Text)
    buying_agent_name = Column(Text)
    buying_agent_email = Column(Text)

    listing_side_gross_commission = Column(Numeric)
    listing_side_agent_net = Column(Numeric)
    listing_side_brokerage_net = Column(Numeric)

    buying_side_gross_commission = Column(Numeric)
    buying_side_agent_net = Column(Numeric)
    buying_side_brokerage_net = Column(Numeric)

    total_gross_commission = Column(Numeric)
    total_agent_net = Column(Numeric)
    total_brokerage_net = Column(Numeric)

    da_title_company = Column(Text)
    da_closer_name = Column(Text)
    da_mailing_address = Column(Text)
    da_phone = Column(Text)
    da_email = Column(Text)

    mi_mortgage_company = Column(Text)
    mi_lender_name = Column(Text)
    mi_mailing_address = Column(Text)
    mi_phone = Column(Text)
    mi_email = Column(Text)

    referrals = Column(Text)
    tags = Column(Text)

    cancel_date = Column(Date)
    closed_date = Column(Date)
    finalized_date = Column(Date)

    skyslopefileid = Column(UUID(as_uuid=True))

    transaction_specialist = Column(Text)
    transaction_status = Column(String)