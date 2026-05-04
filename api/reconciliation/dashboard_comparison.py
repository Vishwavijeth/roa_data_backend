from fastapi import APIRouter
from services.engine import run_field

router = APIRouter()

@router.get("/comparison/{field}")
def compare(field: str):
    return run_field(field)