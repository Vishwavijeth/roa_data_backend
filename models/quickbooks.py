from sqlalchemy import Column, String, Integer, Numeric, Date, DateTime, func
from db import Base

class QuickbooksInvoice(Base):
    __tablename__ = "quickbooks_invoices"
    
    invoice_id = Column(String, primary_key=True, nullable=False)
    customer_id = Column(String, nullable=False)
    sync_token = Column(Integer, nullable=False, default=0)
    balance = Column(Numeric(14, 2), nullable=False, default=0)
    total_amt = Column(Numeric(14, 2), nullable=False, default=0)
    due_date = Column(Date)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    doc_number = Column(String)
    txn_date = Column(Date)