from fastapi import APIRouter, Depends
from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from common.response import FilterResponse
from db import get_db
from models.skyslope.meta import Checklist, Stage
from models.skyslope.property import SaleProperty
from models.skyslope.sale import Sale
from models.skyslope.users import SkyslopeUser


router = APIRouter()


@router.get(
    "/skyslope-filters",
    response_model=FilterResponse,
)
def skyslope_filters(
    db: Session = Depends(get_db),
):
    # ========================================================
    # REVIEWER NAME
    # ========================================================
    full_name = func.concat_ws(
        " ",
        func.nullif(SkyslopeUser.firstname, ""),
        func.nullif(SkyslopeUser.lastname, ""),
    )

    reviewer_name = case(
        # No reviewer assigned
        (
            Sale.reviewerguid.is_(None),
            "Unassigned",
        ),

        # Reviewer GUID exists but user record does not exist
        (
            SkyslopeUser.userguid.is_(None),
            "No User Record",
        ),

        # User exists but both first name and last name are empty
        (
            (
                SkyslopeUser.firstname.is_(None)
                | (SkyslopeUser.firstname == "")
            )
            & (
                SkyslopeUser.lastname.is_(None)
                | (SkyslopeUser.lastname == "")
            ),
            "No User Record",
        ),

        else_=full_name,
    )

    # ========================================================
    # SALE FILTERS
    #
    # Status, type of sale and reviewer all come from sale.
    # Fetch them together so sale is scanned once.
    # ========================================================
    sale_filters = (
        select(
            func.array_agg(
                distinct(Sale.status)
            )
            .filter(
                Sale.status.is_not(None),
                Sale.status != "",
            )
            .label("status"),

            func.array_agg(
                distinct(Sale.dealtype)
            )
            .filter(
                Sale.dealtype.is_not(None),
                Sale.dealtype != "",
            )
            .label("type_of_sale"),

            func.array_agg(
                distinct(reviewer_name)
            ).label("reviewer"),
        )
        .select_from(Sale)
        .outerjoin(
            SkyslopeUser,
            Sale.reviewerguid == SkyslopeUser.userguid,
        )
        .cte("sale_filters")
    )

    # ========================================================
    # STAGE
    # ========================================================
    stage_query = (
        select(
            func.array_agg(
                distinct(Stage.name)
            )
        )
        .where(
            Stage.name.is_not(None),
            Stage.name != "",
        )
        .scalar_subquery()
    )

    # ========================================================
    # CHECKLIST
    #
    # Checklist is the only one where we trim.
    # TRIM happens in DB before DISTINCT.
    #
    # Example:
    #
    # "Listing Checklist"
    # " Listing Checklist"
    # "Listing Checklist "
    #
    # becomes one "Listing Checklist"
    # ========================================================
    checklist_name = func.trim(Checklist.typename)

    checklist_query = (
        select(
            func.array_agg(
                distinct(checklist_name)
            )
        )
        .where(
            Checklist.typename.is_not(None),
            checklist_name != "",
        )
        .scalar_subquery()
    )

    # ========================================================
    # STATE
    #
    # Normalize only state to uppercase before DISTINCT.
    #
    # TX / Tx / tx -> TX
    # CA / Ca / ca -> CA
    # ========================================================
    normalized_state = func.upper(SaleProperty.state)

    state_query = (
        select(
            func.array_agg(
                distinct(normalized_state)
            )
        )
        .where(
            SaleProperty.state.is_not(None),
            SaleProperty.state != "",
        )
        .scalar_subquery()
    )

    # ========================================================
    # SINGLE DATABASE QUERY
    # ========================================================
    stmt = (
        select(
            sale_filters.c.status,
            sale_filters.c.type_of_sale,
            sale_filters.c.reviewer,
            stage_query.label("stage"),
            checklist_query.label("checklist"),
            state_query.label("state"),
        )
        .select_from(sale_filters)
    )

    result = db.execute(stmt).mappings().one()

    # ========================================================
    # SORT SMALL RESULT SETS IN PYTHON
    #
    # Sorting a few filter values in Python is cheaper than
    # adding ORDER BY work to each database aggregation.
    # ========================================================
    status_list = sorted(
        result["status"] or [],
        key=str.casefold,
    )

    stage_list = sorted(
        result["stage"] or [],
        key=str.casefold,
    )

    checklist_list = sorted(
        result["checklist"] or [],
        key=str.casefold,
    )

    type_of_sale_list = sorted(
        result["type_of_sale"] or [],
        key=str.casefold,
    )

    reviewer_list = sorted(
        result["reviewer"] or [],
        key=str.casefold,
    )

    state_list = sorted(
        result["state"] or []
    )

    # ========================================================
    # RESPONSE
    # ========================================================
    return FilterResponse(
        filters={
            "status": status_list,
            "stage": stage_list,
            "checklist": checklist_list,
            "type_of_sale": type_of_sale_list,
            "reviewer": reviewer_list,
            "state": state_list,
        }
    )