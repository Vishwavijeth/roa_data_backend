from fastapi import APIRouter
import requests

router = APIRouter()


@router.api_route("/data-sync", methods=["GET"])
def data_sync():
    steps = []

    try:
        api1 = requests.post(
            "https://roa-data-backend.vercel.app/sync/brokerage-engine",
            timeout=300,
        )
        steps.append({
            "step": "brokerage_engine_sync",
            "status_code": api1.status_code,
            "success": api1.ok,
        })
        if not api1.ok:
            return {
                "success": False,
                "message": "Stopped at api1. api2, api3, and api4 were not called.",
                "steps": steps,
            }

        api2 = requests.post(
            "https://roa-data-backend.vercel.app/sync/other-income",
            timeout=300,
        )
        steps.append({
            "step": "other_income_sync",
            "status_code": api2.status_code,
            "success": api2.ok,
        })
        if not api2.ok:
            return {
                "success": False,
                "message": "Stopped at api2. api3 and api4 were not called.",
                "steps": steps,
            }

        api3 = requests.post(
            "https://roa-data-backend.vercel.app/sync-skyslope-sales",
            timeout=300,
        )
        steps.append({
            "step": "skyslope_sales_sync",
            "status_code": api3.status_code,
            "success": api3.ok,
        })
        if not api3.ok:
            return {
                "success": False,
                "message": "Stopped at api3. api4 was not called.",
                "steps": steps,
            }

        api4 = requests.post(
            "https://roa-data-backend.vercel.app/reconciliation/data/populate",
            timeout=300,
        )
        steps.append({
            "step": "reconciliation_data_populate",
            "url": "https://roa-data-backend.vercel.app/reconciliation/data/populate",
            "status_code": api4.status_code,
            "success": api4.ok,
        })
        if not api4.ok:
            return {
                "success": False,
                "message": "Stopped at api4.",
                "steps": steps,
            }

        return {
            "success": True,
            "message": "All APIs executed successfully in sequence.",
            "steps": steps,
        }

    except requests.RequestException as e:
        return {
            "success": False,
            "message": "Request failed during sequential sync execution.",
            "steps": steps,
            "error": str(e),
        }