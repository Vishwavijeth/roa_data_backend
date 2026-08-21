from db import Base
from sqlalchemy import Column, String, Date, Numeric, Text, DateTime, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
import uuid

class ReconciliationData(Base):
    __tablename__ = "reconciliation_data"

    transactionid = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    be_source_table = Column(Text)
    saleguid = Column(UUID(as_uuid=True))

    property_address = Column(String)
    be_close_date = Column(Date)
    be_status = Column(Text)
    be_transaction_specialist = Column(String)
    skyslope_reviewer = Column(String)

    # Gross commission
    be_gross_commission = Column(Numeric)
    skyslope_gross_commission = Column(Numeric)
    gross_commission_match = Column(Text)

    # Close date
    be_close_date_value = Column(Date)
    skyslope_close_date_value = Column(Date)
    close_date_match = Column(Text)

    # Status
    be_status_value = Column(Text)
    skyslope_status_value = Column(Text)
    status_match = Column(Text)

    # Sale price
    be_sale_price = Column(Numeric)
    skyslope_sale_price = Column(Numeric)
    sale_price_match = Column(Text)

    # Listing price
    be_listing_price = Column(Numeric)
    skyslope_listing_price = Column(Numeric)
    listing_price_match = Column(Text)

    # Contract date
    be_contract_date = Column(Date)
    skyslope_contract_date = Column(Date)
    contract_date_match = Column(Text)

    # Buyer name
    be_buyer_name = Column(String)
    skyslope_buyer_name = Column(String)
    buyer_name_match = Column(Text)

    # Seller name
    be_seller_name = Column(String)
    skyslope_seller_name = Column(String)
    seller_name_match = Column(Text)

    # Buying agent
    be_buying_agent_name = Column(String)
    skyslope_buying_agent_name = Column(String)
    buying_agent_match = Column(Text)

    # Title company
    be_title_company = Column(String)
    skyslope_title_company = Column(String)
    title_company_match = Column(Text)

    evaluated_at = Column(DateTime)

class ReconciliationReview(Base):
    __tablename__ = "reconciliation_review"

    transactionid = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    review_status = Column(String, nullable=True)
    notes = Column(String, nullable=True)
    updated_by = Column(String, nullable=True)
    updated_at = Column(TIMESTAMP, nullable=True)