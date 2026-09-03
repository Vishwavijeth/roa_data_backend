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
    REPLACEMENT = "Replacement"
    CANCELLED = "Cancelled"
    LEFT_ROA = "Left ROA"
    PAID = "Paid"

    @classmethod
    def values(cls) -> list[str]:
        return [status.value for status in cls]

    @classmethod
    def active_values(cls) -> list[str]:
        return [
            cls.PENDING.value,
            cls.PENDING_PARTIAL.value,
            cls.WAGE_GARNISHMENT.value,
            cls.REPLACEMENT.value,
        ]