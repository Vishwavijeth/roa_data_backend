from fastapi import APIRouter
from db import get_conn
from psycopg2.extras import RealDictCursor

router = APIRouter()

from fastapi import APIRouter, Query
from psycopg2.extras import RealDictCursor

router = APIRouter()

ALLOWED_STAGE_FILTERS = [
    "CDA Eligible - Full Compliance",
    "CDA Eligible - Pending Docs",
    "Missing Docs from Agent",
]


@router.get("/reviewer_listing")
def reviewer_listing(
    stage_name: str = Query(
        default="all",
        description="Filter by stage name"
    )
):
    conn = get_conn()

    try:
        params = []

        query = """
        SELECT
            s.saleguid AS saleguid,

            CONCAT_WS(', ',
                CONCAT_WS(' ', sp.streetnumber, sp.streetaddress),
                sp.city,
                sp.state,
                sp.zip
            ) AS propertyaddress,

            s.saleprice AS sale_price,
            s.listingprice AS listing_price,
            s.escrowclosingdate AS escrow_close_date,

            s.status AS ss_status,

            st.name AS stage_name,

            COALESCE(r.firstname || ' ' || r.lastname, '') AS reviewer_name

        FROM sale s

        LEFT JOIN sale_property sp
            ON s.saleguid = sp.saleguid

        LEFT JOIN users r
            ON s.reviewerguid = r.userguid

        LEFT JOIN stage st
            ON s.stageid = st.stageid
        """

        # apply filter only if not "all"
        if stage_name != "all":

            # validation
            if stage_name not in ALLOWED_STAGE_FILTERS:
                return {
                    "success": False,
                    "message": "Invalid stage_name filter",
                    "allowed_filters": ALLOWED_STAGE_FILTERS
                }

            query += " WHERE st.name = %s "
            params.append(stage_name)

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

        return {
            "success": True,
            "selected_filter": stage_name,
            "data": rows
        }

    finally:
        conn.close()