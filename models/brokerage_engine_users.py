from sqlalchemy import Column, Integer, String, Date, Text
from sqlalchemy.dialects.postgresql import UUID
from db import Base

class BrokerageEngineUser(Base):
    __tablename__ = "brokerage_engine_users"

    display_name = Column(String)
    roa_email = Column(Text)
    personguid = Column(UUID)
    companystaffguid = Column(UUID)
    agenttags = Column(Text)
    portal_agent_id = Column(Text)
    agent_status = Column(String)
    qb_customerid = Column(Integer)
    phone_number = Column(String)
    office = Column(Text)
    start_date = Column(Date)
    general_notes = Column(Text)
    internal_notes = Column(Text)
    agent_identifier = Column(UUID, primary_key=True)