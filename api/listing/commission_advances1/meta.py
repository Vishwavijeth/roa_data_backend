from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.response import Response
from db import get_db
from models.commission_advances.commission_advances1 import CommissionAdvance
from api.listing.commission_advances1.utils import CommissionAdvanceOperation, CommissionAdvanceStatus


router = APIRouter(prefix="/commission-advances")


@router.get(
    "/meta",
    response_model=Response[dict[str, Any]],
)
def get_commission_advance_meta(
    db: Session = Depends(get_db),
):
    try:
        companies = db.execute(
            select(CommissionAdvance.company)
            .where(
                CommissionAdvance.company.isnot(None),
                func.trim(CommissionAdvance.company) != "",
            )
            .distinct()
            .order_by(CommissionAdvance.company.asc())
        ).scalars().all()

        operations = [
            CommissionAdvanceOperation.PAYMENT.value,
            CommissionAdvanceOperation.INTEREST.value,
            CommissionAdvanceOperation.FEE.value,
            CommissionAdvanceOperation.AMENDMENT.value,
        ]

        return Response[dict[str, Any]](
            success=True,
            data={
                "status": [
                    item.value
                    for item in CommissionAdvanceStatus
                ],
                "company": companies,
                "operations": operations,
            },
            message="Metadata fetched successfully",
        )

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch metadata",
        ) from exc