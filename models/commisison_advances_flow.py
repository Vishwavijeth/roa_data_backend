from sqlalchemy import Column, String, Text, Numeric, Date, Boolean
from sqlalchemy.dialects.postgresql import VARCHAR
from db import Base

class CommissionAdvancesFlow(Base):
    __tablename__ = 'commission_advances_flow'
    
    id = Column(VARCHAR(50), primary_key=True)
    type = Column(VARCHAR(50), nullable=False)
    address = Column(Text, nullable=True)
    listing_office = Column(VARCHAR(255), nullable=True)
    sales_office = Column(VARCHAR(255), nullable=True)
    listing_agent_portal_id = Column(VARCHAR(100), nullable=True)
    buying_agent_portal_id = Column(VARCHAR(100), nullable=True)
    price = Column(Numeric(15, 2), nullable=False, default=0.00)
    gci = Column(Numeric(15, 2), nullable=False, default=0.00)
    amount = Column(Numeric(15, 2), nullable=False, default=0.00)
    contract_on = Column(Date, nullable=True)
    closed_on = Column(Date, nullable=True)
    status = Column(VARCHAR(50))
    approved_for_commission = Column(Boolean, nullable=False, default=False)
    approved_for_processing = Column(Boolean, nullable=False, default=False)
    is_other_income = Column(Boolean, nullable=False, default=False)
    commission_deposit_account = Column(VARCHAR(255), nullable=True)
    commission_deposit_account_id = Column(VARCHAR(50), nullable=True)