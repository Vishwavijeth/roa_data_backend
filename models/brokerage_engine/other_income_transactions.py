from sqlalchemy import Column, Text, String, Date, Numeric
from sqlalchemy.dialects.postgresql import UUID
from db import Base

class BEOtherIncomeTransaction(Base):
    __tablename__ = "otherincome_transactions"

    transaction_identifier_transactionid = Column(
        UUID(as_uuid=True),
        primary_key=True,
    )

    transaction_identifier_transactionguid = Column(
        UUID(as_uuid=True)
    )

    listingguid = Column(UUID(as_uuid=True))

    property_address = Column(Text)

    transaction_status = Column(String(50))

    address_line1 = Column(Text)
    address_line2 = Column(Text)

    city = Column(String(100))
    state = Column(String(50))
    zip = Column(String(20))

    property_type = Column(String(100))
    property_subtype = Column(String(100))

    office = Column(Text)

    income_type = Column(String(100))
    income_received_date = Column(Date)

    income_received = Column(Numeric(18, 2))

    agents = Column(Text)

    gross_commission = Column(Numeric(18, 2))
    agent_net = Column(Numeric(18, 2))
    brokerage_net = Column(Numeric(18, 2))

    agents_identifier = Column(Text)

    client_type = Column(String(100))
    client_name = Column(String(200))
    client_phone = Column(String(50))
    client_email = Column(String(200))

    tags = Column(Text)

    effective_at = Column(Date)
    finalized_date = Column(Date)

    transaction_specialist = Column(String(200))

    skyslopefileid = Column(String(100))