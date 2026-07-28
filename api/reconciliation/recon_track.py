from fastapi import APIRouter, Query, Body, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from db import get_db
from pydantic import BaseModel
from typing import Optional
from uuid import UUID

router = APIRouter()

class ReconTrackBody(BaseModel):
    track_status: str
    assigned_to: Optional[str] = None
    notes: Optional[str] = None
    updated_by: Optional[str] = None


@router.post("/reconciliation/track")
def track_reconciliation(
    transaction_id: UUID = Query(...),
    parameter: str = Query(...),
    req: ReconTrackBody = Body(...),
    db: Session = Depends(get_db)
):
    try:
        db.execute(
            text("""
                INSERT INTO public.reconciliation_tracking (
                    transaction_id,
                    parameter,
                    track_status,
                    assigned_to,
                    notes,
                    updated_at,
                    updated_by
                )
                VALUES (:transaction_id, :parameter, :track_status, :assigned_to, :notes, CURRENT_TIMESTAMP, :updated_by)
                ON CONFLICT (transaction_id, parameter)
                DO UPDATE SET
                    track_status = EXCLUDED.track_status,
                    assigned_to = EXCLUDED.assigned_to,
                    notes = EXCLUDED.notes,
                    updated_at = CURRENT_TIMESTAMP,
                    updated_by = EXCLUDED.updated_by;
            """),
            {
                "transaction_id": str(transaction_id),
                "parameter": parameter,
                "track_status": req.track_status,
                "assigned_to": req.assigned_to,
                "notes": req.notes,
                "updated_by": req.updated_by,
            }
        )
        db.commit()

        return {
            "status": "success",
            "message": "Reconciliation status updated successfully."
        }

    except Exception as e:
        db.rollback()
        return {
            "status": "error",
            "message": str(e)
        }