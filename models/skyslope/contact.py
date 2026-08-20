from sqlalchemy import Boolean, Numeric, String, ForeignKey, Integer, Column
from sqlalchemy.dialects.postgresql import UUID
from db import Base

class SaleContact(Base):
    __tablename__ = "sale_contact"
    saleGuid = Column(UUID(as_uuid=True), ForeignKey("sale.saleGuid", ondelete="CASCADE"), primary_key=True, nullable=False)
    contactGuid = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    role = Column(String, primary_key=True, nullable=False)
    firstName = Column(String, nullable=True)
    lastName = Column(String, nullable=True)
    phoneNumber = Column(String, nullable=True)
    email = Column(String, nullable=True)
    company = Column(String, nullable=True)
    alternatePhone = Column(String, nullable=True)
    streetNumber = Column(String, nullable=True)
    streetName = Column(String, nullable=True)
    zip = Column(String, nullable=True)
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    fax = Column(String, nullable=True)
    notes = Column(String, nullable=True)
    isTrustCompanyOrOtherEntity = Column(Boolean, nullable=True)
    isCashDeal = Column(Boolean, nullable=True)
    loanTypeId = Column(Integer, nullable=True)
    loanType = Column(String, nullable=True)
    loanAmount = Column(Numeric, nullable=True)
    brokerTaxId = Column(Integer, nullable=True)
    miscContactType = Column(String, nullable=True)


class SaleCoAgent(Base):
    __tablename__ = "sale_co_agent"
    saleGuid = Column(UUID(as_uuid=True), ForeignKey("sale.saleGuid", ondelete="CASCADE"), primary_key=True, nullable=False)
    coAgentGuid = Column(UUID(as_uuid=True), ForeignKey("users.userGuid"), primary_key=True, nullable=False)


class SaleTransactionCoordinator(Base):
    __tablename__ = "sale_transaction_coordinator"
    saleGuid = Column(UUID(as_uuid=True), ForeignKey("sale.saleGuid", ondelete="CASCADE"), primary_key=True, nullable=False)
    contactGuid = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    firstName = Column(String, nullable=True)
    lastName = Column(String, nullable=True)
    fullName = Column(String, nullable=True)
    email = Column(String, nullable=True)
    phoneNumber = Column(String, nullable=True)
    notes = Column(String, nullable=True)
    fee = Column(Numeric, nullable=True)
    hasAccess = Column(Boolean, nullable=True)