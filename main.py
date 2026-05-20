from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from api.listing.brokerage_engine import router as brokerage_router
from api.listing.skyslope import router as skyslope_router
from api.reconciliation.dashboard_compare import router as compare_router
from api.reconciliation.dashboard_comparison import router as comparison_router
from api.listing.transaction_specialist import router as specialist_router
from api.listing.reviewer import router as reviewer_router
from api.dashboards.transaction_specialist import router as trans_dash_router
from api.listing.brokeage_engine_sync import router as brokerage_sync_router
from api.listing.skyslope_sync import router as skyslope_sync_router
from api.listing.skyslope_sync_logs import router as skyslope_sync_logs_router
from api.dashboards.reviewer import router as review_dash_router
from api.listing.cda_sent import router as cda_sent_router
from api.listing.month_closing import router as month_closing_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
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
app.include_router(skyslope_sync_router)
app.include_router(skyslope_sync_logs_router)
app.include_router(cda_sent_router)
app.include_router(month_closing_router)

from mangum import Mangum

handler = Mangum(app)