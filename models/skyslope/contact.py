from sqlalchemy import Boolean, Numeric, String, ForeignKey, Integer, Column
from sqlalchemy.dialects.postgresql import UUID
from db import Base

class SaleContact(Base):
    __tablename__ = "sale_contact"

    saleguid = Column(UUID(as_uuid=True), ForeignKey("sale.saleguid", ondelete="CASCADE"), primary_key=True, nullable=False)
    contactguid = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    role = Column(String, primary_key=True, nullable=False)
    firstname = Column(String, nullable=True)
    lastname = Column(String, nullable=True)
    phonenumber = Column(String, nullable=True)
    email = Column(String, nullable=True)
    company = Column(String, nullable=True)
    alternatephone = Column(String, nullable=True)
    streetnumber = Column(String, nullable=True)
    streetname = Column(String, nullable=True)
    zip = Column(String, nullable=True)
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    fax = Column(String, nullable=True)
    notes = Column(String, nullable=True)
    istrustcompanyorotherentity = Column(Boolean, nullable=True)
    iscashdeal = Column(Boolean, nullable=True)
    loantypeid = Column(Integer, nullable=True)
    loantype = Column(String, nullable=True)
    loanamount = Column(Numeric, nullable=True)
    brokertaxid = Column(Integer, nullable=True)
    misccontacttype = Column(String, nullable=True)


class SaleCoAgent(Base):
    __tablename__ = "sale_co_agent"

    saleguid = Column(UUID(as_uuid=True), ForeignKey("sale.saleguid", ondelete="CASCADE"), primary_key=True, nullable=False)
    coagentguid = Column(UUID(as_uuid=True), ForeignKey("users.userguid"), primary_key=True, nullable=False)


class SaleTransactionCoordinator(Base):
    __tablename__ = "sale_transaction_coordinator"

    saleguid = Column(UUID(as_uuid=True), ForeignKey("sale.saleguid", ondelete="CASCADE"), primary_key=True, nullable=False)
    contactguid = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    firstname = Column(String, nullable=True)
    lastname = Column(String, nullable=True)
    fullname = Column(String, nullable=True)
    email = Column(String, nullable=True)
    phonenumber = Column(String, nullable=True)
    notes = Column(String, nullable=True)
    fee = Column(Numeric, nullable=True)
    hasaccess = Column(Boolean, nullable=True)