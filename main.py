from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from api.listing.brokerage_engine import router as brokerage_router
from api.listing.skyslope import router as skyslope_router
from api.reconciliation.dashboard_compare import router as compare_router
from api.reconciliation.dashboard_comparison import router as comparison_router
from api.listing.transaction_specialist import router as specialist_router
from api.listing.reviewer import router as reviewer_router
from api.dashboards.transaction_specialist import router as trans_dash_router
from api.listing.brokeage_engine_sync import router as brokerage_sync_router
from api.dashboards.reviewer import router as review_dash_router

app = FastAPI()

ALLOWED_ORIGINS = [
    "https://roa-data-ui.vercel.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global exception handler — ensures CORS headers are present even on 500 errors.
# Without this, an unhandled exception bypasses the CORS middleware response,
# causing the browser to report a CORS error instead of the actual server error.
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
    )

app.include_router(brokerage_router)
app.include_router(skyslope_router)
app.include_router(compare_router)
app.include_router(specialist_router)
app.include_router(reviewer_router)
app.include_router(trans_dash_router)
app.include_router(review_dash_router)
app.include_router(comparison_router)
app.include_router(brokerage_sync_router)