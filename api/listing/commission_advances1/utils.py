from enum import Enum


class CommissionAdvanceOperation(str, Enum):
    BASE_AMOUNT = "Base Amount"
    PAYMENT = "Payment"
    INTEREST = "Interest"
    FEE = "Fee"
    ADJUSTMENT = "Adjustment"
    AMENDMENT = "Amendment"
    WAGE_GARNISHMENT = "Wage Garnishment"
    GARNISHMENT_BALANCE = "Garnishment Balance"


class CommissionAdvanceTransactionType(str, Enum):
    CREDIT = "Credit"
    DEBIT = "Debit"
    STATUS = "Status"

class CommissionAdvanceGarnishmentStatus(str, Enum):
    ACTIVE = "Active"
    SETTLED = "Settled"

class CommissionAdvanceStatus(str, Enum):
    PENDING = "Pending"
    PENDING_PARTIAL = "Pending Partial"
    WAGE_GARNISHMENT = "Wage Garnishment"
    PAID = "Paid"
    REPLACEMENT = "Replacement"
    CANCELLED = "Cancelled"
    LEFT_ROA = "Left ROA"