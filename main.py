from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from services.engine import run_field

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