from sqlalchemy import Boolean, Date, Numeric, ForeignKey, Column
from sqlalchemy.dialects.postgresql import UUID
from db import Base

class SaleEarnestMoneyDeposit(Base):
    __tablename__ = "sale_earnest_money_deposit"
    saleGuid = Column(UUID(as_uuid=True), ForeignKey("sale.saleGuid", ondelete="CASCADE"), primary_key=True, nullable=False)
    isEarnestMoneyHeld = Column(Boolean, nullable=True)
    depositAmount = Column(Numeric, nullable=True)
    depositDueDate = Column(Date, nullable=True)
    datePostedToLogBook = Column(Date, nullable=True)
    dateOfCheck = Column(Date, nullable=True)
    additionalDepositAmount = Column(Numeric, nullable=True)
    additionalDepositDueDate = Column(Date, nullable=True)