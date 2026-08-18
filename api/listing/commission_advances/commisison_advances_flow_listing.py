from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.orm import Session, aliased
from sqlalchemy import func, select
from typing import List, Optional, Generic, TypeVar
from pydantic import BaseModel, Field
from datetime import date
from decimal import Decimal
import logging
from db import get_db
from models.commisison_advances_flow import CommissionAdvancesFlow
from models.brokerage_engine_users import BrokerageEngineUser
from common.pagination import PaginationData, PaginationResponseWithCount
from api.listing.commission_advances.base import CommissionAdvanceListResponse, AgentInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/commission-advances-flow")

def parse_agent_ids(agent_ids_str: Optional[str]) -> List[str]:
    """Parse comma-separated agent IDs."""
    if not agent_ids_str:
        return []
    return [aid.strip() for aid in agent_ids_str.split(',') if aid.strip()]


def fetch_agents_info(db: Session, agent_ids: List[str]) -> dict:
    """Fetch agent info for a list of portal_agent_ids. Returns dict keyed by portal_agent_id."""
    if not agent_ids:
        return {}
    
    results = db.execute(
        select(
            BrokerageEngineUser.portal_agent_id,
            BrokerageEngineUser.display_name,
            BrokerageEngineUser.agent_status
        ).where(
            BrokerageEngineUser.portal_agent_id.in_(agent_ids)
        )
    ).all()
    
    return {
        r.portal_agent_id: {
            'display_name': r.display_name,
            'agent_status': r.agent_status
        }
        for r in results
    }


@router.get("/list", response_model=PaginationResponseWithCount[CommissionAdvanceListResponse])
async def list_commission_advances(
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    type_filter: Optional[str] = Query(None, alias="type"),
    office_filter: Optional[str] = Query(None, alias="office")
):
    try:
        # Build base query
        query = select(
            CommissionAdvancesFlow.type,
            CommissionAdvancesFlow.address,
            CommissionAdvancesFlow.listing_office,
            CommissionAdvancesFlow.sales_office,
            CommissionAdvancesFlow.listing_agent_portal_id,
            CommissionAdvancesFlow.buying_agent_portal_id,
            CommissionAdvancesFlow.price,
            CommissionAdvancesFlow.gci,
            CommissionAdvancesFlow.amount,
            CommissionAdvancesFlow.contract_on,
            CommissionAdvancesFlow.closed_on,
            CommissionAdvancesFlow.status,
            CommissionAdvancesFlow.approved_for_commission,
            CommissionAdvancesFlow.approved_for_processing,
            CommissionAdvancesFlow.is_other_income,
            CommissionAdvancesFlow.commission_deposit_account,
            CommissionAdvancesFlow.commission_deposit_account_id
        )
        
        # Apply filters
        if status_filter:
            query = query.where(CommissionAdvancesFlow.status == status_filter.lower())
        if type_filter:
            query = query.where(CommissionAdvancesFlow.type == type_filter)
        if office_filter:
            query = query.where(
                (CommissionAdvancesFlow.listing_office.ilike(f"%{office_filter}%")) |
                (CommissionAdvancesFlow.sales_office.ilike(f"%{office_filter}%"))
            )
        
        # Get total count of all records
        count_query = select(func.count(CommissionAdvancesFlow.id))
        if status_filter:
            count_query = count_query.where(CommissionAdvancesFlow.status == status_filter.lower())
        if type_filter:
            count_query = count_query.where(CommissionAdvancesFlow.type == type_filter)
        if office_filter:
            count_query = count_query.where(
                (CommissionAdvancesFlow.listing_office.ilike(f"%{office_filter}%")) |
                (CommissionAdvancesFlow.sales_office.ilike(f"%{office_filter}%"))
            )
        
        total_count = db.execute(count_query).scalar()
        
        # Apply pagination
        offset = (page - 1) * page_size
        query = query.order_by(CommissionAdvancesFlow.closed_on.desc(), CommissionAdvancesFlow.id.desc())
        query = query.offset(offset).limit(page_size)
        
        # Execute query
        results = db.execute(query).all()
        
        # Collect all agent IDs to fetch in batch
        all_listing_agent_ids = set()
        all_buying_agent_ids = set()
        
        for r in results:
            all_listing_agent_ids.update(parse_agent_ids(r.listing_agent_portal_id))
            all_buying_agent_ids.update(parse_agent_ids(r.buying_agent_portal_id))
        
        # Fetch all agent info in batch
        listing_agents_info = fetch_agents_info(db, list(all_listing_agent_ids))
        buying_agents_info = fetch_agents_info(db, list(all_buying_agent_ids))
        
        # Calculate pagination
        total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
        has_next = page < total_pages
        
        # Build response
        items = []
        for r in results:
            # Build listing agents list
            listing_agents = []
            for agent_id in parse_agent_ids(r.listing_agent_portal_id):
                agent_info = listing_agents_info.get(agent_id, {})
                listing_agents.append(AgentInfo(
                    portal_agent_id=agent_id,
                    display_name=agent_info.get('display_name'),
                    agent_status=agent_info.get('agent_status')
                ))
            
            # Build buying agents list
            buying_agents = []
            for agent_id in parse_agent_ids(r.buying_agent_portal_id):
                agent_info = buying_agents_info.get(agent_id, {})
                buying_agents.append(AgentInfo(
                    portal_agent_id=agent_id,
                    display_name=agent_info.get('display_name'),
                    agent_status=agent_info.get('agent_status')
                ))
            
            items.append(CommissionAdvanceListResponse(
                type=r.type,
                address=r.address,
                listing_office=r.listing_office,
                sales_office=r.sales_office,
                listing_agent_portal_id=r.listing_agent_portal_id,
                buying_agent_portal_id=r.buying_agent_portal_id,
                price=r.price,
                gci=r.gci,
                amount=r.amount,
                contract_on=r.contract_on,
                closed_on=r.closed_on,
                status=r.status,
                approved_for_commission=r.approved_for_commission,
                approved_for_processing=r.approved_for_processing,
                is_other_income=r.is_other_income,
                commission_deposit_account=r.commission_deposit_account,
                commission_deposit_account_id=r.commission_deposit_account_id,
                listing_agents=listing_agents,
                buying_agents=buying_agents
            ))
        
        return PaginationResponseWithCount[CommissionAdvanceListResponse](
            success=True,
            data=PaginationData(total_count=total_count, items=items),
            page=page,
            page_size=page_size,
            count=len(items),
            total_pages=total_pages,
            has_next=has_next,
            message="Request successful"
        )
        
    except Exception as e:
        logger.error(f"List failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))