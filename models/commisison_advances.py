from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, Date
from db import Base

class CommissionAdvance(Base):
    __tablename__ = "commission_advances"

    
    agent_name: Mapped[str | None] = mapped_column(String)
    state: Mapped[str | None] = mapped_column(String)
    amount: Mapped[int | None] = mapped_column(Integer)
    address: Mapped[str | None] = mapped_column(String)
    company: Mapped[str | None] = mapped_column(String)
    paid_date: Mapped[Date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String)