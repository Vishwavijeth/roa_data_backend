from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from db import get_db
from models.skyslope.meta import Checklist, Office, Stage
from models.skyslope.property import SaleProperty
from models.skyslope.sale import Sale
from services.states import STATE_FULL_NAME_MAP

router = APIRouter()

STATE_CODE_BY_FULL_NAME = {
    full_name.upper(): state_code
    for state_code, full_name in STATE_FULL_NAME_MAP.items()
}


def normalized_checklist_type_expr():
    return func.lower(
        func.trim(
            func.coalesce(Checklist.typename, "")
        )
    )


def property_address_expr():
    return func.concat_ws(
        " ",
        SaleProperty.streetnumber,
        SaleProperty.streetaddress,
        SaleProperty.unit,
        SaleProperty.city,
        SaleProperty.state,
        SaleProperty.zip,
    )


def checklist_validation_expr():
    """
    Recreates the original checklist/deal type validation CASE expression.

    Returns:
        "match"
        "mismatch"
        NULL
    """
    checklist_type = normalized_checklist_type_expr()

    return case(
        (
            Sale.dealtype == "Listing",
            case(
                (
                    or_(
                        checklist_type.like("%listing%"),
                        checklist_type.like("%seller%"),
                    ),
                    "match",
                ),
                else_="mismatch",
            ),
        ),
        (
            Sale.dealtype == "Both Purchase & Listing",
            case(
                (
                    or_(
                        checklist_type.like("%dual%"),
                        checklist_type.like("%intermediary%"),
                    ),
                    "match",
                ),
                else_="mismatch",
            ),
        ),
        (
            Sale.dealtype == "Lease Tenant",
            case(
                (
                    or_(
                        checklist_type.like("%lease%"),
                        checklist_type.like("%tenant%"),
                        checklist_type.like("%rental%"),
                        checklist_type.like("%apartment%"),
                    ),
                    "match",
                ),
                else_="mismatch",
            ),
        ),
        (
            Sale.dealtype == "Lease Landlord",
            case(
                (
                    checklist_type.like("%landlord%"),
                    "match",
                ),
                else_="mismatch",
            ),
        ),
        (
            Sale.dealtype == "Referral",
            case(
                (
                    checklist_type.like("%referral%"),
                    "match",
                ),
                else_="mismatch",
            ),
        ),
        (
            Sale.dealtype == "BPO",
            case(
                (
                    checklist_type.like("%bpo%"),
                    "match",
                ),
                else_="mismatch",
            ),
        ),
        (
            Sale.dealtype == "Both Lease Tenant & Landlord",
            case(
                (
                    checklist_type.like("%tx | lease intermediary%"),
                    "match",
                ),
                else_="mismatch",
            ),
        ),
        (
            Sale.dealtype.in_(["Purchase", "Other"]),
            None,
        ),
        else_=None,
    )


# ============================================================
# COMMON ORM QUERY / FILTERS
# ============================================================

def checklist_validation_base_stmt(*columns):
    return (
        select(*columns)
        .select_from(Sale)
        .outerjoin(
            SaleProperty,
            Sale.saleguid == SaleProperty.saleguid,
        )
        .outerjoin(
            Checklist,
            Sale.checklisttypeid == Checklist.typeid,
        )
        .outerjoin(
            Stage,
            Sale.stageid == Stage.stageid,
        )
        .outerjoin(
            Office,
            Sale.officeguid == Office.officeguid,
        )
    )


def apply_checklist_validation_filters(
    stmt,
    *,
    state: list[str] | None = None,
    stage_name: list[str] | None = None,
    status: list[str] | None = None,
    type_of_sale: list[str] | None = None,
    checklist_type: list[str] | None = None,
    search: str | None = None,
):
    conditions = [
        checklist_validation_expr() == "mismatch"
    ]

    # --------------------------------------------------------
    # State
    #
    # State values come from sale_property for the dropdown,
    # but filtering is based on office name.
    #
    # TX -> TX% OR Texas%
    # CA -> CA% OR California%
    # --------------------------------------------------------
    if state:
        state_conditions = []

        for value in state:
            if not value or not value.strip():
                continue

            selected_state = value.strip().upper()

            state_code = STATE_CODE_BY_FULL_NAME.get(
                selected_state,
                selected_state,
            )

            full_state_name = STATE_FULL_NAME_MAP.get(state_code)

            if not full_state_name:
                continue

            state_conditions.append(
                or_(
                    Office.officename.ilike(f"{state_code}%"),
                    Office.officename.ilike(f"{full_state_name}%"),
                )
            )

        if state_conditions:
            conditions.append(or_(*state_conditions))

    # --------------------------------------------------------
    # Stage
    # --------------------------------------------------------
    if stage_name:
        cleaned_stage_names = [
            value.strip()
            for value in stage_name
            if value and value.strip()
        ]

        if cleaned_stage_names:
            conditions.append(
                func.trim(
                    func.coalesce(Stage.name, "")
                ).in_(cleaned_stage_names)
            )

    # --------------------------------------------------------
    # Status
    # --------------------------------------------------------
    if status:
        cleaned_statuses = [
            value.strip()
            for value in status
            if value and value.strip()
        ]

        if cleaned_statuses:
            conditions.append(
                func.trim(
                    func.coalesce(Sale.status, "")
                ).in_(cleaned_statuses)
            )

    # --------------------------------------------------------
    # Type of sale
    # --------------------------------------------------------
    if type_of_sale:
        cleaned_types = [
            value.strip()
            for value in type_of_sale
            if value and value.strip()
        ]

        if cleaned_types:
            conditions.append(
                func.trim(
                    func.coalesce(Sale.dealtype, "")
                ).in_(cleaned_types)
            )

    # --------------------------------------------------------
    # Checklist type
    # --------------------------------------------------------
    if checklist_type:
        cleaned_checklist_types = list({
            value.strip().lower()
            for value in checklist_type
            if value and value.strip()
        })

        if cleaned_checklist_types:
            conditions.append(
                normalized_checklist_type_expr().in_(
                    cleaned_checklist_types
                )
            )

    # --------------------------------------------------------
    # Search by property address
    # --------------------------------------------------------
    if search and search.strip():
        conditions.append(
            property_address_expr().ilike(
                f"%{search.strip()}%"
            )
        )

    return stmt.where(and_(*conditions))


# ============================================================
# FILTER METADATA
# ============================================================

@router.get("/checklist-type-validation/filters")
def checklist_type_validation_filters(
    db: Session = Depends(get_db),
):
    # --------------------------------------------------------
    # Type of sale
    # --------------------------------------------------------
    type_of_sale_value = func.trim(Sale.dealtype).label(
        "dealtype"
    )

    type_of_sale_stmt = (
        select(type_of_sale_value)
        .where(
            Sale.dealtype.is_not(None),
            func.trim(Sale.dealtype) != "",
        )
        .distinct()
        .order_by(type_of_sale_value)
    )

    # --------------------------------------------------------
    # State
    #
    # Collect distinct states from sale_property.
    # These are only dropdown values.
    # --------------------------------------------------------
    state_value = func.upper(
        func.trim(SaleProperty.state)
    ).label("state")

    state_stmt = (
        select(state_value)
        .where(
            SaleProperty.state.is_not(None),
            func.trim(SaleProperty.state) != "",
        )
        .distinct()
        .order_by(state_value)
    )

    # --------------------------------------------------------
    # Status
    # --------------------------------------------------------
    status_value = func.trim(Sale.status).label("status")

    status_stmt = (
        select(status_value)
        .where(
            Sale.status.is_not(None),
            func.trim(Sale.status) != "",
        )
        .distinct()
        .order_by(status_value)
    )

    # --------------------------------------------------------
    # Stage
    # --------------------------------------------------------
    stage_value = func.trim(Stage.name).label("name")

    stage_stmt = (
        select(stage_value)
        .where(
            Stage.name.is_not(None),
            func.trim(Stage.name) != "",
        )
        .distinct()
        .order_by(stage_value)
    )

    # --------------------------------------------------------
    # Checklist type
    # --------------------------------------------------------
    checklist_value = func.trim(
        Checklist.typename
    ).label("typename")

    checklist_stmt = (
        select(checklist_value)
        .where(
            Checklist.typename.is_not(None),
            func.trim(Checklist.typename) != "",
        )
        .distinct()
        .order_by(checklist_value)
    )

    return {
        "filters": {
            "type_of_sale": list(
                db.scalars(type_of_sale_stmt).all()
            ),
            "state": list(
                db.scalars(state_stmt).all()
            ),
            "status": list(
                db.scalars(status_stmt).all()
            ),
            "stage_name": list(
                db.scalars(stage_stmt).all()
            ),
            "checklist_type": list(
                db.scalars(checklist_stmt).all()
            ),
        }
    }


# ============================================================
# CHECKLIST TYPE VALIDATION DATA
# ============================================================

@router.get("/checklist-type-validation")
def checklist_type_validation_data(
    page: int = Query(default=1, ge=1),
    state: list[str] | None = Query(default=None),
    stage_name: list[str] | None = Query(default=None),
    status: list[str] | None = Query(default=None),
    type_of_sale: list[str] | None = Query(default=None),
    checklist_type: list[str] | None = Query(default=None),
    search: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    limit = 50
    offset = (page - 1) * limit

    # --------------------------------------------------------
    # Count
    # --------------------------------------------------------
    count_stmt = checklist_validation_base_stmt(
        func.count().label("total_count")
    )

    count_stmt = apply_checklist_validation_filters(
        count_stmt,
        state=state,
        stage_name=stage_name,
        status=status,
        type_of_sale=type_of_sale,
        checklist_type=checklist_type,
        search=search,
    )

    total_count = db.scalar(count_stmt) or 0

    # --------------------------------------------------------
    # Data
    # --------------------------------------------------------
    data_stmt = checklist_validation_base_stmt(
        Sale.saleguid.label("saleguid"),
        Sale.url.label("url"),
        property_address_expr().label("propertyaddress"),
        func.trim(
            func.coalesce(Office.officename, "")
        ).label("office_name"),
        func.trim(
            func.coalesce(Sale.status, "")
        ).label("status"),
        func.trim(
            func.coalesce(Sale.dealtype, "")
        ).label("type_of_sale"),
        func.trim(
            func.coalesce(Checklist.typename, "")
        ).label("checklist_type_name"),
        checklist_validation_expr().label("match_result"),
    )

    data_stmt = apply_checklist_validation_filters(
        data_stmt,
        state=state,
        stage_name=stage_name,
        status=status,
        type_of_sale=type_of_sale,
        checklist_type=checklist_type,
        search=search,
    )

    data_stmt = (
        data_stmt
        .order_by(Sale.saleguid)
        .limit(limit)
        .offset(offset)
    )

    rows = db.execute(data_stmt).mappings().all()

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
        "data": [
            dict(row)
            for row in rows
        ],
    }
