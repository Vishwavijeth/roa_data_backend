from fastapi import APIRouter, Query, Depends
from db import get_db
from psycopg2.extras import RealDictCursor

router = APIRouter()

@router.get("/reviewer_listing")
def reviewer_listing(
    stage_name: str = Query(
        default="all",
        description="Filter by stage name"
    ),
    conn=Depends(get_db)
):
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            # 1. get available stages dynamically
            cur.execute("""
                SELECT DISTINCT name
                FROM stage
                WHERE name IS NOT NULL
                ORDER BY name;
            """)
            stage_filters = [row["name"] for row in cur.fetchall()]

            # 2. main query
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

            # 3. apply filter if needed
            if stage_name != "all":
                query += " WHERE st.name = %s "
                params.append(stage_name)

            query += " ORDER BY s.saleguid "

            cur.execute(query, params)
            rows = cur.fetchall()

        return {
            "success": True,
            "selected_filter": stage_name,
            "data": rows
        }

    finally:
        pass