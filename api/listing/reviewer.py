from fastapi import APIRouter, Query, Depends
from db import get_db
from psycopg2.extras import RealDictCursor

router = APIRouter()

@router.get("/reviewer_listing")
def reviewer_listing(
    stage_name: list[str] | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    from_close_date: str = Query(default=None),
    to_close_date: str = Query(default=None),
    state: list[str] | None = Query(default=None),
    status: list[str] | None = Query(default=None),
    reviewer: list[str] | None = Query(default=None),
    conn=Depends(get_db)
):
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    limit = 50
    offset = (page - 1) * limit

    base_query = """
        FROM sale s
        LEFT JOIN sale_property sp
            ON s.saleguid = sp.saleguid
        LEFT JOIN users r
            ON s.reviewerguid = r.userguid
        LEFT JOIN stage st
            ON s.stageid = st.stageid
        WHERE 1=1
    """

    params = []

    if stage_name:
        stage_name = [x for x in stage_name if x]
        if stage_name:
            base_query += " AND st.name = ANY(%s)"
            params.append(stage_name)

    if from_close_date:
        base_query += " AND s.escrowclosingdate >= %s"
        params.append(from_close_date)

    if to_close_date:
        base_query += " AND s.escrowclosingdate <= %s"
        params.append(to_close_date)

    if state:
        state = [x for x in state if x]
        if state:
            base_query += " AND sp.state = ANY(%s)"
            params.append(state)

    if status:
        status = [x for x in status if x]
        if status:
            base_query += " AND s.status = ANY(%s)"
            params.append(status)

    if reviewer:
        reviewer = [x for x in reviewer if x]

        if reviewer:
            non_unassigned_reviewers = [x for x in reviewer if x != "Unassigned"]
            has_unassigned = "Unassigned" in reviewer

            reviewer_conditions = []

            if non_unassigned_reviewers:
                reviewer_conditions.append("""
                    COALESCE(NULLIF(TRIM(CONCAT_WS(' ', r.firstname, r.lastname)), ''), 'Unassigned') = ANY(%s)
                """)
                params.append(non_unassigned_reviewers)

            if has_unassigned:
                reviewer_conditions.append("s.reviewerguid IS NULL")

            if reviewer_conditions:
                base_query += " AND (" + " OR ".join(reviewer_conditions) + ")"

    count_query = "SELECT COUNT(*) AS total_count " + base_query
    cursor.execute(count_query, params)
    total_count = cursor.fetchone()["total_count"]

    data_query = """
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
            CASE
                WHEN s.reviewerguid IS NULL THEN 'Unassigned'
                ELSE COALESCE(NULLIF(TRIM(CONCAT_WS(' ', r.firstname, r.lastname)), ''), 'Unassigned')
            END AS reviewer_name,
            sp.state AS state
    """ + base_query

    data_query += " ORDER BY s.saleguid"
    data_query += " LIMIT %s OFFSET %s"

    data_params = params + [limit, offset]

    cursor.execute(data_query, data_params)
    rows = cursor.fetchall()

    stage_query = """
        SELECT DISTINCT name
        FROM stage
        WHERE name IS NOT NULL
        ORDER BY name
    """
    cursor.execute(stage_query)
    stage_list = [row["name"] for row in cursor.fetchall()]

    state_query = """
        SELECT DISTINCT state
        FROM sale_property
        WHERE state IS NOT NULL AND state <> ''
        ORDER BY state
    """
    cursor.execute(state_query)
    state_list = [row["state"] for row in cursor.fetchall()]

    status_query = """
        SELECT DISTINCT status
        FROM sale
        WHERE status IS NOT NULL AND status <> ''
        ORDER BY status
    """
    cursor.execute(status_query)
    status_list = [row["status"] for row in cursor.fetchall()]

    reviewer_query = """
        SELECT DISTINCT reviewer_name
        FROM (
            SELECT
                CASE
                    WHEN s.reviewerguid IS NULL THEN 'Unassigned'
                    ELSE COALESCE(NULLIF(TRIM(CONCAT_WS(' ', u.firstname, u.lastname)), ''), 'Unassigned')
                END AS reviewer_name
            FROM sale s
            LEFT JOIN users u
                ON s.reviewerguid = u.userguid
        ) x
        ORDER BY reviewer_name
    """
    cursor.execute(reviewer_query)
    reviewer_list = [row["reviewer_name"] for row in cursor.fetchall()]

    return {
        "total_count": total_count,
        "filters": {
            "stage_list": stage_list,
            "state_list": state_list,
            "status_list": status_list,
            "reviewer_list": reviewer_list
        },
        "data": rows
    }