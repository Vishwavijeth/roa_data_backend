from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session
from services.state_office_mapping import STATE_OFFICES_MAP
from db import get_db

router = APIRouter()

def checker_filters_to_named_params(params_list, param_names):
    """Helper to convert list params to named dict params"""
    return dict(zip(param_names, params_list))


def build_checklist_validation_query(
    state: list[str] | None = None,
    stage_name: list[str] | None = None,
    status: list[str] | None = None,
    type_of_sale: list[str] | None = None,
    checklist_type: list[str] | None = None,
    search: str | None = None,
):
    params = {}

    validation_case = """
        CASE
            WHEN s.dealtype = 'Listing' THEN
                CASE
                    WHEN LOWER(TRIM(COALESCE(c.typename, ''))) LIKE '%%listing%%'
                         OR LOWER(TRIM(COALESCE(c.typename, ''))) LIKE '%%seller%%'
                    THEN 'match'
                    ELSE 'mismatch'
                END

            WHEN s.dealtype = 'Both Purchase & Listing' THEN
                CASE
                    WHEN LOWER(TRIM(COALESCE(c.typename, ''))) LIKE '%%dual%%'
                         OR LOWER(TRIM(COALESCE(c.typename, ''))) LIKE '%%intermediary%%'
                    THEN 'match'
                    ELSE 'mismatch'
                END

            WHEN s.dealtype = 'Lease Tenant' THEN
                CASE
                    WHEN LOWER(TRIM(COALESCE(c.typename, ''))) LIKE '%%lease%%'
                         OR LOWER(TRIM(COALESCE(c.typename, ''))) LIKE '%%tenant%%'
                         OR LOWER(TRIM(COALESCE(c.typename, ''))) LIKE '%%rental%%'
                         OR LOWER(TRIM(COALESCE(c.typename, ''))) LIKE '%%apartment%%'
                    THEN 'match'
                    ELSE 'mismatch'
                END

            WHEN s.dealtype = 'Lease Landlord' THEN
                CASE
                    WHEN LOWER(TRIM(COALESCE(c.typename, ''))) LIKE '%%landlord%%'
                    THEN 'match'
                    ELSE 'mismatch'
                END

            WHEN s.dealtype = 'Referral' THEN
                CASE
                    WHEN LOWER(TRIM(COALESCE(c.typename, ''))) LIKE '%%referral%%'
                    THEN 'match'
                    ELSE 'mismatch'
                END

            WHEN s.dealtype = 'BPO' THEN
                CASE
                    WHEN LOWER(TRIM(COALESCE(c.typename, ''))) LIKE '%%bpo%%'
                    THEN 'match'
                    ELSE 'mismatch'
                END

            WHEN s.dealtype = 'Both Lease Tenant & Landlord' THEN
                CASE
                    WHEN LOWER(TRIM(COALESCE(c.typename, ''))) LIKE '%%tx | lease intermediary%%'
                    THEN 'match'
                    ELSE 'mismatch'
                END

            WHEN s.dealtype IN ('Purchase', 'Other') THEN NULL
            ELSE NULL
        END
    """

    property_address_expr = """
        CONCAT_WS(', ',
            CONCAT_WS(' ', sp.streetnumber, sp.streetaddress),
            sp.city,
            sp.state,
            sp.zip
        )
    """

    base_query = f"""
        FROM sale s
        LEFT JOIN sale_property sp
            ON s.saleguid = sp.saleguid
        LEFT JOIN checklist c
            ON s.checklisttypeid = c.typeid
        LEFT JOIN stage st
            ON s.stageid = st.stageid
        LEFT JOIN office o
            ON s.officeguid = o.officeguid
        WHERE ({validation_case}) = 'mismatch'
    """

    if state:
        cleaned_states = sorted({
            x.strip().upper()
            for x in state
            if x and x.strip()
        })

        if cleaned_states:
            mapped_offices = []
            for state_code in cleaned_states:
                mapped_offices.extend(STATE_OFFICES_MAP.get(state_code, []))

            mapped_offices = list({
                office_name.strip()
                for office_name in mapped_offices
                if office_name and office_name.strip()
            })

            if mapped_offices:
                base_query += " AND TRIM(COALESCE(o.officename, '')) = ANY(:checklist_offices)"
                params["checklist_offices"] = mapped_offices
            else:
                base_query += " AND 1=0"

    if stage_name:
        cleaned_stage_names = [x.strip() for x in stage_name if x and x.strip()]
        if cleaned_stage_names:
            base_query += " AND TRIM(COALESCE(st.name, '')) = ANY(:checklist_stages)"
            params["checklist_stages"] = cleaned_stage_names

    if status:
        cleaned_status = [x.strip() for x in status if x and x.strip()]
        if cleaned_status:
            base_query += " AND TRIM(COALESCE(s.status, '')) = ANY(:checklist_status)"
            params["checklist_status"] = cleaned_status

    if type_of_sale:
        cleaned_type_of_sale = [x.strip() for x in type_of_sale if x and x.strip()]
        if cleaned_type_of_sale:
            base_query += " AND TRIM(COALESCE(s.dealtype, '')) = ANY(:checklist_dealtype)"
            params["checklist_dealtype"] = cleaned_type_of_sale

    if checklist_type:
        cleaned_checklist_types = list({
            x.strip().lower()
            for x in checklist_type
            if x and x.strip()
        })
        if cleaned_checklist_types:
            base_query += " AND LOWER(TRIM(COALESCE(c.typename, ''))) = ANY(:checklist_types)"
            params["checklist_types"] = cleaned_checklist_types

    if search and search.strip():
        base_query += f" AND {property_address_expr} ILIKE :checklist_search"
        params["checklist_search"] = f"%{search.strip()}%"

    return base_query, params, validation_case, property_address_expr


@router.get("/checklist-type-validation/filters")
def checklist_type_validation_filters(db: Session = Depends(get_db)):
    try:
        type_of_sale_query = """
            SELECT DISTINCT TRIM(dealtype) AS dealtype
            FROM sale
            WHERE dealtype IS NOT NULL AND TRIM(dealtype) <> ''
            ORDER BY dealtype
        """

        state_query = """
            SELECT DISTINCT UPPER(TRIM(state)) AS state
            FROM sale_property
            WHERE state IS NOT NULL AND TRIM(state) <> ''
            ORDER BY state
        """

        status_query = """
            SELECT DISTINCT TRIM(status) AS status
            FROM sale
            WHERE status IS NOT NULL AND TRIM(status) <> ''
            ORDER BY status
        """

        stage_query = """
            SELECT DISTINCT TRIM(name) AS name
            FROM stage
            WHERE name IS NOT NULL AND TRIM(name) <> ''
            ORDER BY name
        """

        checklist_query = """
            SELECT DISTINCT TRIM(typename) AS typename
            FROM checklist
            WHERE typename IS NOT NULL AND TRIM(typename) <> ''
            ORDER BY typename
        """

        type_of_sale_list = [row["dealtype"] for row in db.execute(text(type_of_sale_query)).mappings().all()]
        state_list = [row["state"] for row in db.execute(text(state_query)).mappings().all()]
        status_list = [row["status"] for row in db.execute(text(status_query)).mappings().all()]
        stage_list = [row["name"] for row in db.execute(text(stage_query)).mappings().all()]
        checklist_type_list = [row["typename"] for row in db.execute(text(checklist_query)).mappings().all()]

        return {
            "filters": {
                "type_of_sale": type_of_sale_list,
                "state": state_list,
                "status": status_list,
                "stage_name": stage_list,
                "checklist_type": checklist_type_list
            }
        }

    finally:
        pass


@router.get("/checklist-type-validation")
def checklist_type_validation_data(
    page: int = Query(default=1, ge=1),
    state: list[str] | None = Query(default=None),
    stage_name: list[str] | None = Query(default=None),
    status: list[str] | None = Query(default=None),
    type_of_sale: list[str] | None = Query(default=None),
    checklist_type: list[str] | None = Query(default=None),
    search: str | None = Query(default=None),
    db: Session = Depends(get_db)
):
    try:
        limit = 50
        offset = (page - 1) * limit

        base_query, params, validation_case, property_address_expr = build_checklist_validation_query(
            state=state,
            stage_name=stage_name,
            status=status,
            type_of_sale=type_of_sale,
            checklist_type=checklist_type,
            search=search,
        )

        count_query = f"""
            SELECT COUNT(*) AS total_count
            {base_query}
        """

        data_query = f"""
            SELECT
                s.saleguid,
                s.url AS url,
                {property_address_expr} AS propertyaddress,
                TRIM(COALESCE(o.officename, '')) AS office_name,
                TRIM(COALESCE(s.status, '')) AS status,
                TRIM(COALESCE(s.dealtype, '')) AS type_of_sale,
                TRIM(COALESCE(c.typename, '')) AS checklist_type_name,
                ({validation_case}) AS match_result
            {base_query}
            ORDER BY s.saleguid
            LIMIT :checklist_limit OFFSET :checklist_offset
        """

        data_params = {**params, "checklist_limit": limit, "checklist_offset": offset}

        total_count = db.execute(text(count_query), params).scalar()
        rows = db.execute(text(data_query), data_params).mappings().all()

        return {
            "total_count": total_count,
            "page": page,
            "page_size": limit,
            "applied_filters": {
                "state": state,
                "stage_name": stage_name,
                "status": status,
                "type_of_sale": type_of_sale,
                "checklist_type": checklist_type,
                "search": search,
            },
            "data": [dict(row) for row in rows]
        }

    finally:
        pass