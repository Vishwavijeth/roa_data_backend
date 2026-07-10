from fastapi import APIRouter, Query, Depends
import io
import datetime
import pandas as pd
from fastapi.responses import Response
from db import get_db
from services.state_office_mapping import STATE_OFFICES_MAP
from services.reviewer_filters import apply_common_filters
from psycopg2.extras import RealDictCursor
from decimal import Decimal
from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter


router = APIRouter()

@router.get("/reviewer-listing")
def reviewer_listing(
    stage_name: list[str] | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    from_close_date: str = Query(default=None),
    to_close_date: str = Query(default=None),
    state: list[str] | None = Query(default=None),
    status: list[str] | None = Query(default=None),
    reviewer: list[str] | None = Query(default=None),
    type_of_sale: list[str] | None = Query(default=None),
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
        LEFT JOIN office o
            ON s.officeguid = o.officeguid
        WHERE 1=1
    """

    params = []

    base_query, params = apply_common_filters(
        query=base_query,
        params=params,
        from_date=from_close_date,
        to_date=to_close_date,
        state=state,
        stage_name=stage_name,
        status=status,
        reviewer=reviewer,
        type_of_sale=type_of_sale,
        date_field="s.escrowclosingdate",
    )

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
            o.officename AS office,
            s.dealtype AS type_of_sale
    """ + base_query + """
        ORDER BY s.saleguid
        LIMIT %s OFFSET %s
    """

    data_params = params + [limit, offset]

    cursor.execute(data_query, data_params)
    rows = cursor.fetchall()

    return {
        "total_count": total_count,
        "page": page,
        "page_size": limit,
        "data": rows
    }

@router.get("/reviewer-listing/download")
def download_reviewer_listing(
    stage_name: list[str] | None = Query(default=None),
    from_close_date: str = Query(default=None),
    to_close_date: str = Query(default=None),
    state: list[str] | None = Query(default=None),
    status: list[str] | None = Query(default=None),
    reviewer: list[str] | None = Query(default=None),
    type_of_sale: list[str] | None = Query(default=None),
    conn=Depends(get_db)
):
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    base_query = """
        FROM sale s
        LEFT JOIN sale_property sp
            ON s.saleguid = sp.saleguid
        LEFT JOIN users r
            ON s.reviewerguid = r.userguid
        LEFT JOIN stage st
            ON s.stageid = st.stageid
        LEFT JOIN office o
            ON s.officeguid = o.officeguid
        WHERE 1=1
    """

    params = []

    base_query, params = apply_common_filters(
        query=base_query,
        params=params,
        from_date=from_close_date,
        to_date=to_close_date,
        state=state,
        stage_name=stage_name,
        status=status,
        reviewer=reviewer,
        type_of_sale=type_of_sale,
        date_field="s.escrowclosingdate",
    )

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
            o.officename AS office,
            s.dealtype AS type_of_sale
    """ + base_query + """
        ORDER BY s.saleguid
    """

    cursor.execute(data_query, params)
    data = cursor.fetchall()

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
    for r in data:
        row_dict = {}
        for key, header in columns_map.items():
            val = r.get(key)

            if isinstance(val, Decimal):
                val = float(val)
            elif isinstance(val, (datetime.date, datetime.datetime)):
                val = val.strftime("%Y-%m-%d")
            elif isinstance(val, bool):
                val = "Yes" if val else "No"
            elif val is None:
                val = ""

            row_dict[header] = val

        rows_to_export.append(row_dict)

    df = pd.DataFrame(rows_to_export)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Reviewer Listing", index=False)

        worksheet = writer.sheets["Reviewer Listing"]
        worksheet.freeze_panes = "A2"

        font_header = Font(name="Segoe UI", size=11, bold=True)
        align_header = Alignment(horizontal="center", vertical="center", wrap_text=True)
        font_body = Font(name="Segoe UI", size=10)

        worksheet.row_dimensions[1].height = 28
        for col_num in range(1, len(df.columns) + 1):
            cell = worksheet.cell(row=1, column=col_num)
            cell.font = font_header
            cell.alignment = align_header

        currency_cols = []
        date_cols = []
        center_cols = []

        currency_keywords = ["sale price", "listing price"]
        date_keywords = ["date"]
        center_keywords = ["sale guid", "status", "stage", "reviewer", "office", "type of sale"]

        for idx, col_name in enumerate(df.columns):
            col_name_lower = col_name.lower()
            if any(kw in col_name_lower for kw in currency_keywords):
                currency_cols.append(idx + 1)
            elif any(kw in col_name_lower for kw in date_keywords):
                date_cols.append(idx + 1)
            elif any(kw in col_name_lower for kw in center_keywords):
                center_cols.append(idx + 1)

        for row_num in range(2, len(df) + 2):
            worksheet.row_dimensions[row_num].height = 20

            for col_num in range(1, len(df.columns) + 1):
                cell = worksheet.cell(row=row_num, column=col_num)
                cell.font = font_body

                val = cell.value

                if col_num in currency_cols:
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                    if isinstance(val, (int, float)):
                        cell.number_format = '$#,##0.00'
                elif col_num in date_cols:
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                elif col_num in center_cols:
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="center")

        for col in worksheet.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)

            for cell in col:
                val = cell.value
                if val is not None:
                    if cell.column in currency_cols and isinstance(val, (int, float)):
                        val_len = len(f"${val:,.2f}")
                    else:
                        val_len = len(str(val))
                    if val_len > max_len:
                        max_len = val_len

            worksheet.column_dimensions[col_letter].width = max(max_len + 3, 12)

    output.seek(0)

    filename = "reviewer_listing_report.xlsx"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"'
    }

    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers
    )