from fastapi import APIRouter
from db import get_conn
from psycopg2.extras import RealDictCursor

router = APIRouter()

@router.get("/reviewer_listing")
def reviewer_listing():
    conn = get_conn()

    try:
        query = """
        SELECT
            s.saleguid AS saleguid,

            -- build property address
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

            NULL AS be_workflow_status,

            -- reviewer name
            COALESCE(r.firstname || ' ' || r.lastname, '') AS reviewer_name

        FROM sale s

        LEFT JOIN sale_property sp
            ON s.saleguid = sp.saleguid

        LEFT JOIN users r
            ON s.reviewerguid = r.userguid

        ORDER BY s.saleguid;
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()

        return {
            "count": len(rows),
            "data": rows
        }

    finally:
        conn.close()