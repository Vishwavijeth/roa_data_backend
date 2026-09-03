from pydantic import BaseModel, ConfigDict, field_validator, Field
from api.listing.commission_advances.utils import CommissionAdvanceOperation, CommissionAdvanceStatus, CommissionAdvanceTransactionType
from datetime import date
from typing import Optional, List
from decimal import Decimal
from uuid import UUID


class UpdateCommissionAdvanceRequest(BaseModel):
    status: CommissionAdvanceStatus
    amount: Decimal | None = None
    operation: CommissionAdvanceOperation | None = None
    type: CommissionAdvanceTransactionType | None = None
    approved_date: date | None = None
    paid_date: date | None = None
    notes: str | None = None
    transaction_date: date | None = None
    saleguid: UUID | None = None
    address: str | None = None

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str | None) -> str | None:
        if value is None:
            return None

        value = value.strip()

        return value or None


class CommissionAdvanceTransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ca_id: int
    operation: str
    type: str
    amount: Decimal
    transaction_date: date | None
    notes: str | None
    created_by: str | None


class CommissionAdvanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_name: str | None
    state: str | None
    address: str | None
    company: str | None
    original_amount: Decimal | None
    amount_paid: Decimal | None
    status: str | None
    outstanding_amount: Decimal | None
    paid_date: date | None
    notes: str | None
    approved_date: date | None


class UpdateCommissionAdvanceResponse(BaseModel):
    commission_advance: CommissionAdvanceResponse
    transaction: CommissionAdvanceTransactionResponse | None = None


class CommissionAdvanceSummary(BaseModel):
    pending_advances: int
    commission_advance_received: int
    agents_with_active_advances: int


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


