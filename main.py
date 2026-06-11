from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from api.listing.brokerage_engine import router as brokerage_router
from api.listing.otherincome_transactions import router as other_income_listing_router
from api.listing.skyslope import router as skyslope_router
from api.listing.transaction_specialist import router as transaction_specialist_router
from api.listing.reviewer import router as reviewer_router
from api.dashboards.transaction_specialist import router as trans_dash_router
from api.listing.brokeage_engine_sync import router as brokerage_sync_router
from api.listing.other_income_sync import router as other_income_sync_router
from api.listing.skyslope_sync import router as skyslope_sync_router
from api.listing.skyslope_sync_logs import router as skyslope_sync_logs_router
from api.dashboards.reviewer import router as review_dash_router
from api.listing.cda_sent import router as cda_sent_router
from api.listing.month_closing import router as month_closing_router
from api.reconciliation.sale_price import router as sale_price_router
from api.reconciliation.close_date import router as close_date_router
from api.reconciliation.gross_commission import router as gci_router
from api.reconciliation.status import router as status_router
from api.reconciliation.listing_price import router as listing_price_router
from api.reconciliation.buyer_name import router as buyer_name_router
from api.reconciliation.buying_agent_name import router as buying_agent_name_router
from api.reconciliation.contract_date import router as contract_date_router
from api.reconciliation.seller_name import router as seller_name_router
from api.reconciliation.title_company import router as title_company_router
from api.reconciliation.transaction_reviewer import router as transaction_reviewer_recon_router
from api.reconciliation.other_income import router as other_income_router
from api.reconciliation.recon_track import router as recon_track_router
from api.cron import router as cron_router
from api.listing.brokerhold import router as broker_hold_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(brokerage_router)
app.include_router(other_income_listing_router)
app.include_router(skyslope_router)
app.include_router(transaction_specialist_router)
app.include_router(reviewer_router)
app.include_router(trans_dash_router)
app.include_router(review_dash_router)
app.include_router(brokerage_sync_router)
app.include_router(other_income_sync_router)
app.include_router(skyslope_sync_router)
app.include_router(skyslope_sync_logs_router)
app.include_router(cda_sent_router)
app.include_router(month_closing_router)
app.include_router(sale_price_router)
app.include_router(close_date_router)
app.include_router(gci_router)
app.include_router(status_router)
app.include_router(listing_price_router)
app.include_router(buyer_name_router)
app.include_router(buying_agent_name_router)
app.include_router(contract_date_router)
app.include_router(seller_name_router)
app.include_router(title_company_router)
app.include_router(transaction_reviewer_recon_router)
app.include_router(other_income_router)
app.include_router(recon_track_router)
app.include_router(cron_router)
app.include_router(broker_hold_router)

from mangum import Mangum

handler = Mangum(app)