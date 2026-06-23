from typing import Optional, Literal
from uuid import UUID
from db import get_db
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from psycopg2.extras import RealDictCursor

router = APIRouter()


class ReconciliationReviewCreate(BaseModel):
    review_status: Literal["in_review", "review_done", "not_a_mismatch"]
    notes: Optional[str] = None
    updated_by: Optional[str] = None


@router.post("/reconciliation/review/{transactionid}")
def create_reconciliation_review(
    transactionid: UUID,
    payload: ReconciliationReviewCreate,
    conn=Depends(get_db),
):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO reconciliation_review (
                transactionid,
                review_status,
                notes,
                updated_by,
                updated_at
            )
            VALUES (%s, %s, %s, %s, NOW())
            RETURNING
                transactionid,
                review_status,
                notes,
                updated_by,
                updated_at
            """,
            (
                str(transactionid),
                payload.review_status,
                payload.notes,
                payload.updated_by,
            ),
        )
        row = cur.fetchone()
        conn.commit()

    return {
        "message": "Review added successfully",
        "data": row,
    }