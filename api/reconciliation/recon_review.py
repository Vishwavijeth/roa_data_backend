from typing import Optional, Literal
from uuid import UUID
from sqlalchemy import text
from sqlalchemy.orm import Session
from db import get_db
from fastapi import APIRouter, Depends
from pydantic import BaseModel

router = APIRouter()


class ReconciliationReviewCreate(BaseModel):
    review_status: Literal["in_review", "review_done", "not_a_mismatch"]
    notes: Optional[str] = None
    updated_by: Optional[str] = None


@router.post("/reconciliation/review/{transactionid}")
def create_reconciliation_review(
    transactionid: UUID,
    payload: ReconciliationReviewCreate,
    db: Session = Depends(get_db),
):
    result = db.execute(
        text("""
            INSERT INTO reconciliation_review (
                transactionid,
                review_status,
                notes,
                updated_by,
                updated_at
            )
            VALUES (:transactionid, :review_status, :notes, :updated_by, NOW())
            RETURNING
                transactionid,
                review_status,
                notes,
                updated_by,
                updated_at
        """),
        {
            "transactionid": str(transactionid),
            "review_status": payload.review_status,
            "notes": payload.notes,
            "updated_by": payload.updated_by,
        },
    )
    row = result.mappings().one()
    db.commit()

    return {
        "message": "Review added successfully",
        "data": dict(row),
    }