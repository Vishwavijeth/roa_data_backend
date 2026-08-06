from sqlalchemy import BigInteger, Column, String, Text
from sqlalchemy.dialects.postgresql import UUID
from db import Base


class SkyslopeUser(Base):
    __tablename__ = "users"

    userguid = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    contactid = Column(BigInteger)
    oktauserid = Column(Text)
    firstname = Column(Text)
    lastname = Column(Text)
    email = Column(Text)
    usertype = Column(Text)
    publicid = Column(Text)
    streetnumber = Column(Text)
    streetname = Column(Text)
    zipcode = Column(Text)
    city = Column(Text)
    state = Column(Text)
    phone = Column(Text)
    alternatephone = Column(Text)