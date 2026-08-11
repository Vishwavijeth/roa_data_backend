from sqlalchemy import Boolean, Column, Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from models.skyslope.meta import Office, Checklist, Stage
from models.skyslope.users import SkyslopeUser
from db import Base

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