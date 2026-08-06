from sqlalchemy import (
    Column, Numeric, Text, Integer, Date, String, Computed, text, BigInteger, ForeignKey, DateTime
    )
from sqlalchemy.dialects.postgresql import UUID
from db import Base

class CommissionAdvance(Base):
    __tablename__ = "commission_advances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_name = Column(String(255), nullable=False)
    state = Column(String(2), nullable=False)
    address = Column(String(500), nullable=False)
    company = Column(String(255), nullable=False)
    original_amount = Column(Numeric(12, 2), nullable=False)
    amount_paid = Column(Numeric(12, 2), nullable=False, server_default=text("0"))
    status = Column(String(20), nullable=False, server_default=text("'Pending'"))
    outstanding_amount = Column(
        Numeric(12, 2),
        Computed(
            """
            CASE
                WHEN status IN ('Paid', 'Cancelled') THEN 0
                ELSE original_amount - amount_paid
            END
            """,
            persisted=True,
        ),
    )
    paid_date = Column(Date)
    notes = Column(Text)
    approved_date = Column(Date)
    saleguid = Column(UUID(as_uuid=True))

class CommissionAdvanceHistory(Base):
    __tablename__ = "commission_advances_history"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ca_id = Column(
        Integer,
        ForeignKey("commission_advances.id", ondelete="CASCADE"),
        nullable=False,
    )
    field = Column(String(100), nullable=False)
    old_value = Column(Text)
    new_value = Column(Text)
    action = Column(String(20), nullable=True)
    edited_by = Column(String(255), nullable=False)
    edited_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )