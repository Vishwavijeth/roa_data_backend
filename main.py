from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.listing.brokerage_engine import router as brokerage_router
from api.listing.skyslope import router as skyslope_router
from api.reconciliation.dashboard_compare import router as compare_router
from api.reconciliation.dashboard_comparison import router as comparison_router
from api.listing.transaction_specialist import router as specialist_router
from api.listing.reviewer import router as reviewer_router
from api.dashboards.transaction_specialist import router as trans_dash_router
from api.dashboards.reviewer import router as review_dash_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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