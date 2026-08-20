from sqlalchemy import Boolean, Date, Numeric, String, ForeignKey, Integer, Column
from sqlalchemy.dialects.postgresql import UUID
from db import Base


class SaleCommission(Base):
    __tablename__ = "sale_commission"
    saleGuid = Column(UUID(as_uuid=True), ForeignKey("sale.saleGuid", ondelete="CASCADE"), primary_key=True, nullable=False)
    transactionCoordinatorName = Column(String, nullable=True)
    transactionCoordinatorFee = Column(String, nullable=True)
    adminBrokerageComp = Column(Numeric, nullable=True)
    dateOfCheck = Column(Date, nullable=True)
    datePostedToLogBook = Column(Date, nullable=True)
    listingCommissionPercent = Column(Numeric, nullable=True)
    listingCommissionAmount = Column(Numeric, nullable=True)
    saleCommissionPercent = Column(Numeric, nullable=True)
    saleCommissionAmount = Column(Numeric, nullable=True)
    otherDeductions = Column(Numeric, nullable=True)
    personalDeal = Column(Boolean, nullable=True)
    commissionBreakdownDetails = Column(String, nullable=True)
    officeGrossCommissionOnSale = Column(Numeric, nullable=True)


class SaleCommissionBreakdown(Base):
    __tablename__ = "sale_commission_breakdown"
    id = Column(Integer, primary_key=True, autoincrement=True)
    saleGuid = Column(UUID(as_uuid=True), ForeignKey("sale.saleGuid", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=True)
    details = Column(String, nullable=True)
    amount = Column(Numeric, nullable=True)


class SaleCommissionSplit(Base):
    __tablename__ = "sale_commission_split"
    saleGuid = Column(UUID(as_uuid=True), ForeignKey("sale.saleGuid", ondelete="CASCADE"), primary_key=True, nullable=False)
    agentGuid = Column(UUID(as_uuid=True), ForeignKey("users.userGuid"), primary_key=True, nullable=True)
    amount = Column(Numeric, nullable=True)
    percentage = Column(Numeric, nullable=True)


class SaleCommissionReferral(Base):
    __tablename__ = "sale_commission_referral"
    saleGuid = Column(UUID(as_uuid=True), ForeignKey("sale.saleGuid", ondelete="CASCADE"), primary_key=True, nullable=False)
    typeId = Column(Integer, nullable=True)
    typeName = Column(String, nullable=True)
    contactGuid = Column(UUID(as_uuid=True), nullable=True)
    contactFirstName = Column(String, nullable=True)
    contactLastName = Column(String, nullable=True)
    contactEmail = Column(String, nullable=True)
    contactPhoneNumber = Column(String, nullable=True)
    brokerageName = Column(String, nullable=True)
    amount = Column(Numeric, nullable=True)