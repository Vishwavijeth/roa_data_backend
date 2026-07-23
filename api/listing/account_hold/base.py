from pydantic import BaseModel, Field
from typing import Any

# ----------- Summary -----------
class AccountHoldSummaryData(BaseModel):
    total_agents: int
    agents_with_ar_balance: int
    agents_with_account_hold: int

# ----------- Listing -----------
class AccountHoldItem(BaseModel):
    display_name: str
    primary_emailaddress: str | None = None
    customer_id: str | None = None
    transaction_count: int
    broker_flags: list[str]
    transaction_flags: list[str]

# ----------- Detail -----------
class OpenInvoiceItem(BaseModel):
    balance: Any | None = None
    due_date: str | None = None
    txn_date: str | None = None
    total_amt: Any | None = None
    doc_number: str | None = None
    invoice_id: str | int | None = None


class ARBalanceItem(BaseModel):
    balance: Any | None = None
    open_invoices: list[OpenInvoiceItem] = Field(default_factory=list)


class TransactionItem(BaseModel):
    transactionid: str | int | None = None
    property_address: str | None = None
    source_table: str | None = None
    status: str | None = None
    skyslope_url: str = None
    be_transaction_specialist: str | None = None
    skyslope_reviewer: str | None = None
    transaction_flags: list[str] = Field(default_factory=list)
    mismatch_details: dict[str, Any] = Field(default_factory=dict)


class AccountHoldDetailData(BaseModel):
    display_name: str
    primary_emailaddress: str | None = None
    customer_id: str | None = None
    transaction_count: int
    broker_flags: list[str] = Field(default_factory=list)
    ar_balance: ARBalanceItem | None = None
    transactions: list[TransactionItem] = Field(default_factory=list)