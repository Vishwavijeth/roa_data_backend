from sqlalchemy import Boolean, Date, Numeric, String, ForeignKey, Integer, Column
from sqlalchemy.dialects.postgresql import UUID
from db import Base


class SaleCommission(Base):
    __tablename__ = "sale_commission"

    saleguid = Column(UUID(as_uuid=True), ForeignKey("sale.saleguid", ondelete="CASCADE"), primary_key=True, nullable=False)
    transactioncoordinatorname = Column(String, nullable=True)
    transactioncoordinatorfee = Column(String, nullable=True)
    adminbrokeragecomp = Column(Numeric, nullable=True)
    dateofcheck = Column(Date, nullable=True)
    datepostedtologbook = Column(Date, nullable=True)
    listingcommissionpercent = Column(Numeric, nullable=True)
    listingcommissionamount = Column(Numeric, nullable=True)
    salecommissionpercent = Column(Numeric, nullable=True)
    salecommissionamount = Column(Numeric, nullable=True)
    otherdeductions = Column(Numeric, nullable=True)
    personaldeal = Column(Boolean, nullable=True)
    commissionbreakdowndetails = Column(String, nullable=True)
    officegrosscommissiononsale = Column(Numeric, nullable=True)


class SaleCommissionBreakdown(Base):
    __tablename__ = "sale_commission_breakdown"

    id = Column(Integer, primary_key=True, autoincrement=True)
    saleguid = Column(UUID(as_uuid=True), ForeignKey("sale.saleguid", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=True)
    details = Column(String, nullable=True)
    amount = Column(Numeric, nullable=True)


class SaleCommissionSplit(Base):
    __tablename__ = "sale_commission_split"

    saleguid = Column(UUID(as_uuid=True), ForeignKey("sale.saleguid", ondelete="CASCADE"), primary_key=True, nullable=False)
    agentguid = Column(UUID(as_uuid=True), ForeignKey("users.userguid"), primary_key=True, nullable=True)
    amount = Column(Numeric, nullable=True)
    percentage = Column(Numeric, nullable=True)


class SaleCommissionReferral(Base):
    __tablename__ = "sale_commission_referral"

    saleguid = Column(UUID(as_uuid=True), ForeignKey("sale.saleguid", ondelete="CASCADE"), primary_key=True, nullable=False)
    typeid = Column(Integer, nullable=True)
    typename = Column(String, nullable=True)
    contactguid = Column(UUID(as_uuid=True), nullable=True)
    contactfirstname = Column(String, nullable=True)
    contactlastname = Column(String, nullable=True)
    contactemail = Column(String, nullable=True)
    contactphonenumber = Column(String, nullable=True)
    brokeragename = Column(String, nullable=True)
    amount = Column(Numeric, nullable=True)