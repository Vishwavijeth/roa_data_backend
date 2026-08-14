from pydantic import BaseModel, Field
from typing import Optional
from api.listing.commission_advances.utils import CommissionAdvanceStatus
from decimal import Decimal
from datetime import date
from uuid import UUID

class CommissionAdvanceSummary(BaseModel):
    pending_advances: int
    commission_advance_received: int
    agents_with_active_advances: int

class CommissionAdvanceUpdateRequest(BaseModel):
    status: Optional[CommissionAdvanceStatus] = Field(default=None)
    notes: Optional[str] = None
    amount: Optional[Decimal] = Field(default=None, ge=0)
    paid_date: Optional[date] = None
    approved_date: Optional[date] = None
    edited_by: Optional[str] = Field(default=None, max_length=255)
    saleguid: Optional[UUID] = None
    address: Optional[str] = None