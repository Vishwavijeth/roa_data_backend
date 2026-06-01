from fastapi import APIRouter, Query, Body
from db import get_conn
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
    req: ReconTrackBody = Body(...)
):
    conn = get_conn()

    try:
        query = """
            INSERT INTO public.reconciliation_tracking (
                transaction_id,
                parameter,
                track_status,
                assigned_to,
                notes,
                updated_at,
                updated_by
            )
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s)
            ON CONFLICT (transaction_id, parameter)
            DO UPDATE SET
                track_status = EXCLUDED.track_status,
                assigned_to = EXCLUDED.assigned_to,
                notes = EXCLUDED.notes,
                updated_at = CURRENT_TIMESTAMP,
                updated_by = EXCLUDED.updated_by;
        """

        with conn.cursor() as cur:
            cur.execute(
                query,
                (
                    str(transaction_id),
                    parameter,
                    req.track_status,
                    req.assigned_to,
                    req.notes,
                    req.updated_by
                )
            )
            conn.commit()

        return {
            "status": "success",
            "message": "Reconciliation status updated successfully."
        }

    except Exception as e:
        conn.rollback()
        return {
            "status": "error",
            "message": str(e)
        }

    finally:
        conn.close()