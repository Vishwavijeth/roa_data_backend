from pydantic import BaseModel, ConfigDict, field_validator
from api.listing.commission_advances1.utils import CommissionAdvanceOperation, CommissionAdvanceStatus, CommissionAdvanceTransactionType
from datetime import date
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