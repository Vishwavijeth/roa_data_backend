from pydantic import BaseModel, Field
from typing import Optional, List
from api.listing.commission_advances.utils import CommissionAdvanceStatus, CommissionAdvanceOperation
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
    operation: Optional[CommissionAdvanceOperation] = None
    paid_date: Optional[date] = None
    approved_date: Optional[date] = None
    edited_by: Optional[str] = Field(default=None, max_length=255)
    saleguid: Optional[UUID] = None
    address: Optional[str] = None

class AgentInfo(BaseModel):
    portal_agent_id: str
    display_name: Optional[str]
    agent_status: Optional[str]

class CommissionAdvanceListResponse(BaseModel):
    type: str
    address: Optional[str]
    listing_office: Optional[str]
    sales_office: Optional[str]
    listing_agent_portal_id: Optional[str]
    buying_agent_portal_id: Optional[str]
    price: Decimal
    gci: Decimal
    amount: Decimal
    contract_on: Optional[date]
    closed_on: Optional[date]
    status: str
    approved_for_commission: bool
    approved_for_processing: bool
    is_other_income: bool
    commission_deposit_account: Optional[str]
    commission_deposit_account_id: Optional[str]
    listing_agents: List[AgentInfo] = Field(default_factory=list)
    buying_agents: List[AgentInfo] = Field(default_factory=list)

    class Config:
        from_attributes = True