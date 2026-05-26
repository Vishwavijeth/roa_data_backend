from fastapi import APIRouter
import requests

router = APIRouter()

@router.api_route("/cron-data-sync", methods=["GET"])
def cron_job():
    try:
        # API 1 (POST)
        api1 = requests.post(
            "https://roa-data-backend.vercel.app/sync/brokerage-engine"
        )

        # API 2 (GET with dynamic date)
        api2 = requests.post(
            "https://roa-data-backend.vercel.app/sync/skyslope-sales"
        )

        return {
            "api1_status": api1.status_code,
            "api2_status": api2.status_code
        }

    except Exception as e:
        return {"error": str(e)}