from sqlalchemy import Boolean, Column, Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from models.skyslope_meta import Office, Checklist, Stage
from models.skyslope_users import SkyslopeUser
from db import Base

class Sale(Base):
    __tablename__ = "sale"

    transaction_type = Column(String)
    saleguid = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    listingguid = Column(UUID(as_uuid=True))
    agentguid = Column(UUID(as_uuid=True), ForeignKey("users.userguid"))
    createdbyguid = Column(UUID(as_uuid=True), ForeignKey("users.userguid"))
    mlsnumber = Column(String)
    email = Column(String)
    statusid = Column(Integer)
    status = Column(String)
    officeguid = Column(UUID(as_uuid=True), ForeignKey("office.officeguid"))
    checklisttypeid = Column(Integer, ForeignKey("checklist.typeid"))
    escrownumber = Column(String)
    escrowclosingdate = Column(Date)
    actualclosingdate = Column(Date)
    contractacceptancedate = Column(Date)
    createdon = Column(Date)
    checklistmodifiedon = Column(Date)
    deaddate = Column(Date)
    reviewerguid = Column(UUID(as_uuid=True), ForeignKey("users.userguid"))
    sourceid = Column(Integer)
    source = Column(String)
    othersource = Column(String)
    dealtype = Column(String)
    saletypeid = Column(Integer)
    listingprice = Column(Numeric(15, 4))
    saleprice = Column(Numeric(15, 4))
    isofficelead = Column(Boolean)
    cobrokercompany = Column(String)
    realpropertytype = Column(String)
    realpropertysubtype = Column(String)
    commerciallease = Column(String)
    stageid = Column(Integer, ForeignKey("stage.stageid"))
    customfields = Column(String)
    fileid = Column(String)
    url = Column(String)

class SaleProperty(Base):
    __tablename__ = "sale_property"

    saleguid = Column(UUID(as_uuid=True), ForeignKey("sale.saleguid", ondelete="CASCADE"), primary_key=True, nullable=False)
    streetnumber = Column(Integer)
    streetaddress = Column(String)
    unit = Column(String)
    direction = Column(String)
    city = Column(String)
    county = Column(String)
    state = Column(String)
    zip = Column(String)
    yearbuilt = Column(Integer)
    realpropertytypeid = Column(Integer)
    realpropertysubtypeid = Column(Integer)
    apn = Column(String)