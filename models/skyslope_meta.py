from sqlalchemy import BigInteger, Column, Text, ForeignKey, Integer, Boolean
from sqlalchemy.dialects.postgresql import UUID
from db import Base


class Office(Base):
    __tablename__ = "office"

    officeid = Column(BigInteger)
    officeguid = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    officename = Column(Text)

class UserOffice(Base):
    __tablename__ = "user_office"

    userguid = Column(UUID(as_uuid=True), ForeignKey("users.userguid", ondelete="CASCADE"), primary_key=True, nullable=False)
    officeguid = Column(UUID(as_uuid=True), ForeignKey("office.officeguid", ondelete="CASCADE"), primary_key=True, nullable=False)

class Checklist(Base):
    __tablename__ = "checklist"

    typeid = Column(Integer, primary_key=True, nullable=False)
    typename = Column(Text)

class Stage(Base):
    __tablename__ = "stage"

    stageid = Column(BigInteger, primary_key=True, nullable=False)
    name = Column(Text)
    isdefault = Column(Boolean)