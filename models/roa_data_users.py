# models.py
from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, ForeignKey
from database import Base
from enum import Enum


class UserRole(str, Enum):
    ADMIN = "admin"
    STAFF = "staff"

class RoaDataUserRole(Base):
    __tablename__ = "roa_data_user_roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, nullable=False, index=True)


class RoaDataUser(Base):
    __tablename__ = "roa_data_users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    refresh_token = Column(Text, nullable=True)
    refresh_token_expires_at = Column(DateTime, nullable=True)
    role_id = Column(
        Integer,
        ForeignKey("roa_data_user_roles.id", ondelete="RESTRICT"),
        nullable=False,
    )