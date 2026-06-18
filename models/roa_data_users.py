# models.py
from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime
from database import Base

class RoaDataUser(Base):
    __tablename__ = "roa_data_users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    refresh_token = Column(Text, nullable=True)
    refresh_token_expires_at = Column(DateTime, nullable=True)