from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from services.engine import run_field, run_brokerage_engine

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/comparison/{field}")
def compare(field: str):
    return run_field(field)

@app.get("/brokerage_engine")
def brokerage_engine():
    return run_brokerage_engine()