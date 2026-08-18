from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert
from typing import List
import logging
import httpx
import os
import re
from datetime import datetime
from decimal import Decimal

from db import get_db
from models.commisison_advances_flow import CommissionAdvancesFlow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/commission-advances-flow")

commission_advance_flow_api = os.getenv("COMMISSION_ADVANCES_FLOW_API")
commission_advance_flow_key = os.getenv("COMMISISON_ADVANCES_FLOW_AUTH_KEY")


def parse_currency(value) -> Decimal:
    if value is None:
        return Decimal('0.00')
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    cleaned = re.sub(r'[^\d.\-]', '', str(value))
    if not cleaned:
        return Decimal('0.00')
    return Decimal(cleaned)


def parse_date(date_str: str | None):
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%m/%d/%Y").date()
    except ValueError:
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return None


def extract_agent_portal_ids(agents: List[dict]) -> str | None:
    if not agents:
        return None
    portal_ids = [agent["portalAgentId"] for agent in agents if agent.get("portalAgentId")]
    return ",".join(portal_ids) if portal_ids else None


def transform(api_record: dict) -> dict:
    return {
        'id': api_record.get("_id", ""),
        'type': api_record.get("type", "Sales"),
        'address': api_record.get("address"),
        'listing_office': api_record.get("listingOfficeName"),
        'sales_office': api_record.get("salesOfficeName"),
        'listing_agent_portal_id': extract_agent_portal_ids(api_record.get("listingSideAgent", [])),
        'buying_agent_portal_id': extract_agent_portal_ids(api_record.get("buyingSideAgent", [])),
        'price': float(parse_currency(api_record.get("price", "0"))),
        'gci': float(parse_currency(api_record.get("gci", "0"))),
        'amount': float(parse_currency(api_record.get("amount", "0"))),
        'contract_on': parse_date(api_record.get("contractOn")),
        'closed_on': parse_date(api_record.get("closedOn")),
        'status': api_record.get("status", "pending").lower(),
        'approved_for_commission': api_record.get("approvedForCommission", False),
        'approved_for_processing': api_record.get("approvedForProcessing", False),
        'is_other_income': api_record.get("isOtherIncome", False),
        'commission_deposit_account': api_record.get("commissionDepositAccount"),
        'commission_deposit_account_id': str(api_record.get("commissionDepositAccountId", ""))
    }


async def fetch_api_data() -> List[dict]:
    headers = {
        "Authorization": commission_advance_flow_key,
        "Content-Type": "application/json"
    }
    
    body = {"fromDate": "2025-01-01"}
    
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            commission_advance_flow_api,
            headers=headers,
            json=body
        )
        response.raise_for_status()
        data = response.json()
        return data.get("data", data) if isinstance(data, dict) else data


@router.post("/sync")
async def sync(db: Session = Depends(get_db)):
    """
    Fetch from external API and populate database.
    If id exists: UPDATE, If not: INSERT
    """
    try:
        api_records = await fetch_api_data()
        
        if not api_records:
            return {"total": 0, "inserted": 0, "updated": 0}
        
        records = [transform(r) for r in api_records]
        
        # Check existing IDs
        incoming_ids = [r['id'] for r in records]
        existing_ids = {
            row[0] for row in 
            db.query(CommissionAdvancesFlow.id)
            .filter(CommissionAdvancesFlow.id.in_(incoming_ids))
            .all()
        }
        
        inserted = len(incoming_ids) - len(existing_ids)
        updated = len(existing_ids)
        
        # Bulk upsert
        stmt = pg_insert(CommissionAdvancesFlow).values(records)
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=['id'],
            set_={
                'type': stmt.excluded.type,
                'address': stmt.excluded.address,
                'listing_office': stmt.excluded.listing_office,
                'sales_office': stmt.excluded.sales_office,
                'listing_agent_portal_id': stmt.excluded.listing_agent_portal_id,
                'buying_agent_portal_id': stmt.excluded.buying_agent_portal_id,
                'price': stmt.excluded.price,
                'gci': stmt.excluded.gci,
                'amount': stmt.excluded.amount,
                'contract_on': stmt.excluded.contract_on,
                'closed_on': stmt.excluded.closed_on,
                'status': stmt.excluded.status,
                'approved_for_commission': stmt.excluded.approved_for_commission,
                'approved_for_processing': stmt.excluded.approved_for_processing,
                'is_other_income': stmt.excluded.is_other_income,
                'commission_deposit_account': stmt.excluded.commission_deposit_account,
                'commission_deposit_account_id': stmt.excluded.commission_deposit_account_id
            }
        )
        
        db.execute(upsert_stmt)
        db.commit()
        
        logger.info(f"Sync: {len(records)} records, {inserted} inserted, {updated} updated")
        
        return {"total": len(records), "inserted": inserted, "updated": updated}
        
    except Exception as e:
        logger.error(f"Sync failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))