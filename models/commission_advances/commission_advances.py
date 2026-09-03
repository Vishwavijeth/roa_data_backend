from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from api.listing.commission_advances.utils import CommissionAdvanceGarnishmentStatus
from db import Base


class CommissionAdvance(Base):
    __tablename__ = "commission_advances1"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    agent_name = Column(String(255), nullable=True)
    state = Column(String(2), nullable=True)
    address = Column(String(500), nullable=True)
    company = Column(String(255), nullable=True)
    original_amount = Column(Numeric, nullable=True)
    amount_paid = Column(Numeric, nullable=True)
    status = Column(String(30), nullable=True, index=True)
    outstanding_amount = Column(Numeric, nullable=True)
    paid_date = Column(Date, nullable=True)
    notes = Column(Text, nullable=True)
    approved_date = Column(Date, nullable=True)
    saleguid = Column(UUID(as_uuid=True), nullable=True, index=True)


class CommissionAdvanceGarnishment(Base):
    __tablename__ = "commission_advance_garnishments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(UUID(as_uuid=True), nullable=False)
    agent_name = Column(String(255), nullable=True)
    source_ca_id = Column(Integer, ForeignKey("commission_advances1.id"), nullable=False)
    original_amount = Column(Numeric, nullable=False)
    outstanding_amount = Column(Numeric, nullable=False)
    status = Column(String(20), nullable=False, default=CommissionAdvanceGarnishmentStatus.ACTIVE.value)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    settled_at = Column(DateTime(timezone=True), nullable=True)


class CommissionAdvanceTransaction(Base):
    __tablename__ = "commission_advance_transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ca_id = Column(Integer, ForeignKey("commission_advances1.id"), nullable=False)
    garnishment_id = Column(Integer, ForeignKey("commission_advance_garnishments.id"), nullable=True)
    operation = Column(String(30), nullable=False)
    type = Column(String(10), nullable=False)
    amount = Column(Numeric, nullable=False)
    transaction_date = Column(Date, nullable=True)
    notes = Column(Text, nullable=True)
    outstanding_amount = Column(Numeric, nullable=True)
    created_by = Column(String(255), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())