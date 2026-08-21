from sqlalchemy import Boolean, Date, Numeric, ForeignKey, Column
from sqlalchemy.dialects.postgresql import UUID
from db import Base

class SaleEarnestMoneyDeposit(Base):
    __tablename__ = "sale_earnest_money_deposit"

    saleguid = Column(UUID(as_uuid=True), ForeignKey("sale.saleguid", ondelete="CASCADE"), primary_key=True, nullable=False)
    isearnestmoneyheld = Column(Boolean, nullable=True)
    depositamount = Column(Numeric, nullable=True)
    depositduedate = Column(Date, nullable=True)
    datepostedtologbook = Column(Date, nullable=True)
    dateofcheck = Column(Date, nullable=True)
    additionaldepositamount = Column(Numeric, nullable=True)
    additionaldepositduedate = Column(Date, nullable=True)