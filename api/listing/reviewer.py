from datetime import date, datetime
from decimal import Decimal
import io
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from sqlalchemy import and_, case, distinct, func, or_, select
from sqlalchemy.orm import Session

from db import get_db
from models.skyslope.meta import Office, Stage
from models.skyslope.property import SaleProperty
from models.skyslope.sale import Sale
from models.skyslope.users import SkyslopeUser
from services.states import STATE_FULL_NAME_MAP

router = APIRouter()

STATE_CODE_BY_FULL_NAME = {
    full_name.upper(): state_code
    for state_code, full_name in STATE_FULL_NAME_MAP.items()
}


def reviewer_name_expr():
    """
    Reviewer name rules:

    1. sale.reviewerguid IS NULL
       -> Unassigned

    2. sale.reviewerguid exists but there is no matching users row
       -> No User Record

    3. matching users row exists but firstname + lastname is empty
       -> No User Record

    4. otherwise
       -> firstname + lastname
    """
    full_name = func.nullif(
        func.trim(
            func.concat_ws(
                " ",
                SkyslopeUser.firstname,
                SkyslopeUser.lastname,
            )
        ),
        "",
    )

    return case(
        (Sale.reviewerguid.is_(None), "Unassigned"),
        (SkyslopeUser.userguid.is_(None), "No User Record"),
        (full_name.is_(None), "No User Record"),
        else_=full_name,
    )


def parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None

    return date.fromisoformat(value)


def apply_common_filters_orm(
    stmt,
    *,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    state: Optional[list[str]] = None,
    stage_name: Optional[list[str]] = None,
    status: Optional[list[str]] = None,
    reviewer: Optional[list[str]] = None,
    type_of_sale: Optional[list[str]] = None,
):
    conditions = []

    parsed_from_date = parse_date(from_date)
    parsed_to_date = parse_date(to_date)

    # --------------------------------------------------------
    # Close date
    # --------------------------------------------------------
    if parsed_from_date:
        conditions.append(
            func.date(Sale.escrowclosingdate) >= parsed_from_date
        )

    if parsed_to_date:
        conditions.append(
            func.date(Sale.escrowclosingdate) <= parsed_to_date
        )

    # --------------------------------------------------------
    # State
    #
    # IMPORTANT:
    # The selected state may come from sale_property.state,
    # but the transaction itself is filtered using office name.
    #
    # TX -> office starts with TX OR Texas
    # CA -> office starts with CA OR California
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
        stage_values = [
            value.strip().lower()
            for value in stage_name
            if value and value.strip()
        ]

        if stage_values:
            conditions.append(
                func.lower(func.trim(Stage.name)).in_(stage_values)
            )

    # --------------------------------------------------------
    # Status
    # --------------------------------------------------------
    if status:
        status_values = [
            value.strip().lower()
            for value in status
            if value and value.strip()
        ]

        if status_values:
            conditions.append(
                func.lower(
                    func.trim(func.coalesce(Sale.status, ""))
                ).in_(status_values)
            )

    # --------------------------------------------------------
    # Reviewer
    #
    # Supports:
    # - actual reviewer names
    # - Unassigned
    # - No User Record
    # --------------------------------------------------------
    if reviewer:
        reviewer_values = [
            value.strip()
            for value in reviewer
            if value and value.strip()
        ]

        if reviewer_values:
            conditions.append(
                reviewer_name_expr().in_(reviewer_values)
            )

    # --------------------------------------------------------
    # Type of sale
    # --------------------------------------------------------
    if type_of_sale:
        sale_type_values = [
            value.strip().lower()
            for value in type_of_sale
            if value and value.strip()
        ]

        if sale_type_values:
            conditions.append(
                func.lower(
                    func.trim(func.coalesce(Sale.dealtype, ""))
                ).in_(sale_type_values)
            )

    if conditions:
        stmt = stmt.where(and_(*conditions))

    return stmt


def reviewer_listing_base_stmt(*columns):
    """
    Common FROM/JOIN section used by listing, count, and download.
    """
    return (
        select(*columns)
        .select_from(Sale)
        .outerjoin(
            SaleProperty,
            Sale.saleguid == SaleProperty.saleguid,
        )
        .outerjoin(
            SkyslopeUser,
            Sale.reviewerguid == SkyslopeUser.userguid,
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


def property_address_expr():
    """
    Address format used by /reviewer-listing.
    Preserves the original space-separated response format.
    """
    return func.concat_ws(
        " ",
        SaleProperty.streetnumber,
        SaleProperty.streetaddress,
        SaleProperty.unit,
        SaleProperty.city,
        SaleProperty.state,
        SaleProperty.zip,
    )


def property_address_download_expr():
    """
    Address format used by the Excel download.
    Preserves the original comma-separated format.
    """
    street = func.concat_ws(
        " ",
        SaleProperty.streetnumber,
        SaleProperty.streetaddress,
    )

    return func.concat_ws(
        ", ",
        street,
        SaleProperty.city,
        SaleProperty.state,
        SaleProperty.zip,
    )


# ============================================================
# REVIEWER LISTING
# ============================================================

@router.get("/reviewer-listing")
def reviewer_listing(
    stage_name: list[str] | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    from_close_date: str | None = Query(default=None),
    to_close_date: str | None = Query(default=None),
    state: list[str] | None = Query(default=None),
    status: list[str] | None = Query(default=None),
    reviewer: list[str] | None = Query(default=None),
    type_of_sale: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
):
    limit = 50
    offset = (page - 1) * limit

    # --------------------------------------------------------
    # Count query
    #
    # DISTINCT saleguid prevents joins from inflating the count.
    # --------------------------------------------------------
    count_stmt = reviewer_listing_base_stmt(
        func.count(
            distinct(Sale.saleguid)
        ).label("total_count")
    )

    count_stmt = apply_common_filters_orm(
        count_stmt,
        from_date=from_close_date,
        to_date=to_close_date,
        state=state,
        stage_name=stage_name,
        status=status,
        reviewer=reviewer,
        type_of_sale=type_of_sale,
    )

    total_count = db.scalar(count_stmt) or 0

    # --------------------------------------------------------
    # Listing query
    # --------------------------------------------------------
    data_stmt = reviewer_listing_base_stmt(
        Sale.saleguid.label("saleguid"),
        property_address_expr().label("propertyaddress"),
        Sale.saleprice.label("sale_price"),
        Sale.listingprice.label("listing_price"),
        Sale.escrowclosingdate.label("escrow_close_date"),
        Sale.status.label("ss_status"),
        Stage.name.label("stage_name"),
        reviewer_name_expr().label("reviewer_name"),
        Office.officename.label("office"),
        Sale.dealtype.label("type_of_sale"),
    )

    data_stmt = apply_common_filters_orm(
        data_stmt,
        from_date=from_close_date,
        to_date=to_close_date,
        state=state,
        stage_name=stage_name,
        status=status,
        reviewer=reviewer,
        type_of_sale=type_of_sale,
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
        "data": [dict(row) for row in rows],
    }


# ============================================================
# REVIEWER LISTING DOWNLOAD
# ============================================================

@router.get("/reviewer-listing/download")
def download_reviewer_listing(
    stage_name: list[str] | None = Query(default=None),
    from_close_date: str | None = Query(default=None),
    to_close_date: str | None = Query(default=None),
    state: list[str] | None = Query(default=None),
    status: list[str] | None = Query(default=None),
    reviewer: list[str] | None = Query(default=None),
    type_of_sale: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
):
    data_stmt = reviewer_listing_base_stmt(
        Sale.saleguid.label("saleguid"),
        property_address_download_expr().label("propertyaddress"),
        Sale.saleprice.label("sale_price"),
        Sale.listingprice.label("listing_price"),
        Sale.escrowclosingdate.label("escrow_close_date"),
        Sale.status.label("ss_status"),
        Stage.name.label("stage_name"),
        reviewer_name_expr().label("reviewer_name"),
        Office.officename.label("office"),
        Sale.dealtype.label("type_of_sale"),
    )

    data_stmt = apply_common_filters_orm(
        data_stmt,
        from_date=from_close_date,
        to_date=to_close_date,
        state=state,
        stage_name=stage_name,
        status=status,
        reviewer=reviewer,
        type_of_sale=type_of_sale,
    )

    data_stmt = data_stmt.order_by(Sale.saleguid)

    rows = db.execute(data_stmt).mappings().all()
    data = [dict(row) for row in rows]

    columns_map = {
        "saleguid": "Sale GUID",
        "propertyaddress": "Property Address",
        "sale_price": "Sale Price",
        "listing_price": "Listing Price",
        "escrow_close_date": "Escrow Close Date",
        "ss_status": "Status",
        "stage_name": "Stage Name",
        "reviewer_name": "Reviewer Name",
        "office": "Office",
        "type_of_sale": "Type of Sale",
    }

    rows_to_export = []

    for row in data:
        export_row = {}

        for key, header in columns_map.items():
            value = row.get(key)

            if isinstance(value, Decimal):
                value = float(value)

            elif isinstance(value, (date, datetime)):
                value = value.strftime("%Y-%m-%d")

            elif isinstance(value, bool):
                value = "Yes" if value else "No"

            elif value is None:
                value = ""

            export_row[header] = value

        rows_to_export.append(export_row)

    # Supplying columns explicitly ensures headers are still generated
    # when there are no matching rows.
    df = pd.DataFrame(
        rows_to_export,
        columns=list(columns_map.values()),
    )

    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(
            writer,
            sheet_name="Reviewer Listing",
            index=False,
        )

        worksheet = writer.sheets["Reviewer Listing"]

        for cell in worksheet[1]:
            cell.font = Font(bold=True)

        for column in worksheet.columns:
            max_len = 0
            col_letter = get_column_letter(column[0].column)

            for cell in column:
                if cell.value is not None:
                    max_len = max(
                        max_len,
                        len(str(cell.value)),
                    )

            worksheet.column_dimensions[col_letter].width = max(
                max_len + 2,
                12,
            )

    output.seek(0)

    filename = "reviewer_listing_report.xlsx"

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"'
    }

    return Response(
        content=output.getvalue(),
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        headers=headers,
    )
