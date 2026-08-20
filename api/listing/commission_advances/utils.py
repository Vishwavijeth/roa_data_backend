from enum import Enum

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

class CommissionAdvanceOperation(str, Enum):
    ADD = "add"
    SUB = "sub"
    SET = "set"