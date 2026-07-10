from fastapi import APIRouter, Query, Depends, Response
from typing import Optional, List
from db import get_db
from services.state_office_mapping import STATE_OFFICES_MAP
from services.reviewer_filters import apply_common_filters
from psycopg2.extras import RealDictCursor
from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter
import pandas as pd
import io


router = APIRouter()

@router.get("/reviewer-dashboard")
def reviewer_dashboard(
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    state: Optional[List[str]] = Query(None),
    stage_name: Optional[List[str]] = Query(None),
    status: Optional[List[str]] = Query(None),
    reviewer: Optional[List[str]] = Query(None),
    type_of_sale: Optional[List[str]] = Query(None),
    conn=Depends(get_db)
):
    query = """
    SELECT
        COALESCE(NULLIF(TRIM(CONCAT_WS(' ', r.firstname, r.lastname)), ''), 'Unassigned') AS reviewer_full_name,

        COUNT(*) FILTER (
            WHERE LOWER(TRIM(COALESCE(s.status, ''))) = 'expired'
        ) AS transactions_expired,

        COUNT(*) FILTER (
            WHERE LOWER(TRIM(COALESCE(s.status, ''))) = 'pending'
        ) AS transactions_pending,

        COUNT(*) FILTER (
            WHERE LOWER(TRIM(COALESCE(s.status, ''))) = 'closed'
        ) AS transactions_closed,

        COUNT(*) FILTER (
            WHERE LOWER(TRIM(COALESCE(s.status, ''))) = 'archived'
        ) AS transactions_archived,

        COUNT(*) FILTER (
            WHERE LOWER(TRIM(COALESCE(s.status, ''))) IN ('canceled/app', 'canceled/pend')
        ) AS transactions_canceled,

        COUNT(*) FILTER (
            WHERE LOWER(TRIM(COALESCE(s.status, ''))) = 'incomplete'
        ) AS transactions_incomplete,

        COUNT(*) FILTER (
            WHERE LOWER(TRIM(COALESCE(s.status, ''))) = 'pre-contract'
        ) AS transactions_pre_contract,

        COUNT(*) FILTER (
            WHERE LOWER(TRIM(COALESCE(s.status, ''))) IN (
                'archived',
                'canceled/app',
                'canceled/pend',
                'closed',
                'expired',
                'incomplete',
                'pending',
                'pre-contract'
            )
        ) AS total_transactions

    FROM sale s
    LEFT JOIN users r
        ON s.reviewerguid = r.userguid
    LEFT JOIN sale_property sp
        ON s.saleguid = sp.saleguid
    LEFT JOIN stage st
        ON s.stageid = st.stageid
    LEFT JOIN office o
        ON s.officeguid = o.officeguid
    WHERE 1=1
    """

    params = []

    query, params = apply_common_filters(
        query=query,
        params=[],
        from_date=from_date,
        to_date=to_date,
        state=state,
        stage_name=stage_name,
        status=status,
        reviewer=reviewer,
        type_of_sale=type_of_sale,
        date_field="s.escrowclosingdate",
    )

    query += """
    GROUP BY reviewer_full_name
    ORDER BY reviewer_full_name
    """

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query, tuple(params))
        rows = cur.fetchall()

    summary = {
        "count": len(rows),
        "outstanding_transactions": sum(
            (row.get("transactions_pending", 0) or 0) +
            (row.get("transactions_expired", 0) or 0)
            for row in rows
        ),
        "closed_transactions": sum(
            (row.get("transactions_closed", 0) or 0) +
            (row.get("transactions_archived", 0) or 0)
            for row in rows
        )
    }

    return {
        "summary": summary,
        "data": rows
    }

@router.get("/reviewer-dashboard/download")
def download_reviewer_dashboard(
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    state: Optional[List[str]] = Query(None),
    stage_name: Optional[List[str]] = Query(None),
    status: Optional[List[str]] = Query(None),
    reviewer: Optional[List[str]] = Query(None),
    type_of_sale: Optional[List[str]] = Query(None),
    conn=Depends(get_db)
):
    query = """
    SELECT
        COALESCE(NULLIF(TRIM(CONCAT_WS(' ', r.firstname, r.lastname)), ''), 'Unassigned') AS reviewer_full_name,

        COUNT(*) FILTER (
            WHERE LOWER(TRIM(COALESCE(s.status, ''))) = 'expired'
        ) AS transactions_expired,

        COUNT(*) FILTER (
            WHERE LOWER(TRIM(COALESCE(s.status, ''))) = 'pending'
        ) AS transactions_pending,

        COUNT(*) FILTER (
            WHERE LOWER(TRIM(COALESCE(s.status, ''))) = 'closed'
        ) AS transactions_closed,

        COUNT(*) FILTER (
            WHERE LOWER(TRIM(COALESCE(s.status, ''))) = 'archived'
        ) AS transactions_archived,

        COUNT(*) FILTER (
            WHERE LOWER(TRIM(COALESCE(s.status, ''))) IN ('canceled/app', 'canceled/pend')
        ) AS transactions_canceled,

        COUNT(*) FILTER (
            WHERE LOWER(TRIM(COALESCE(s.status, ''))) = 'incomplete'
        ) AS transactions_incomplete,

        COUNT(*) FILTER (
            WHERE LOWER(TRIM(COALESCE(s.status, ''))) = 'pre-contract'
        ) AS transactions_pre_contract,

        COUNT(*) FILTER (
            WHERE LOWER(TRIM(COALESCE(s.status, ''))) IN (
                'archived',
                'canceled/app',
                'canceled/pend',
                'closed',
                'expired',
                'incomplete',
                'pending',
                'pre-contract'
            )
        ) AS total_transactions

    FROM sale s
    LEFT JOIN users r
        ON s.reviewerguid = r.userguid
    LEFT JOIN sale_property sp
        ON s.saleguid = sp.saleguid
    LEFT JOIN stage st
        ON s.stageid = st.stageid
    LEFT JOIN office o
        ON s.officeguid = o.officeguid
    WHERE 1=1
    """

    params = []

    query, params = apply_common_filters(
        query=query,
        params=params,
        from_date=from_date,
        to_date=to_date,
        state=state,
        stage_name=stage_name,
        status=status,
        reviewer=reviewer,
        type_of_sale=type_of_sale,
        date_field="s.escrowclosingdate",
    )

    query += """
    GROUP BY reviewer_full_name
    ORDER BY reviewer_full_name
    """

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query, tuple(params))
        rows = cur.fetchall()

    rows_to_export = []
    for row in rows:
        rows_to_export.append({
            "Reviewer Name": row.get("reviewer_full_name") or "",
            "Expired": row.get("transactions_expired", 0) or 0,
            "Pending": row.get("transactions_pending", 0) or 0,
            "Closed": row.get("transactions_closed", 0) or 0,
            "Archived": row.get("transactions_archived", 0) or 0,
            "Canceled": row.get("transactions_canceled", 0) or 0,
            "Incomplete": row.get("transactions_incomplete", 0) or 0,
            "Pre-Contract": row.get("transactions_pre_contract", 0) or 0,
            "Total Transactions": row.get("total_transactions", 0) or 0,
        })

    df = pd.DataFrame(rows_to_export)

    if df.empty:
        df = pd.DataFrame(columns=[
            "Reviewer Name",
            "Expired",
            "Pending",
            "Closed",
            "Archived",
            "Canceled",
            "Incomplete",
            "Pre-Contract",
            "Total Transactions",
        ])

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Reviewer Dashboard", index=False)

        worksheet = writer.sheets["Reviewer Dashboard"]
        worksheet.freeze_panes = "A2"

        font_header = Font(name="Segoe UI", size=11, bold=True)
        align_header = Alignment(horizontal="center", vertical="center", wrap_text=True)
        font_body = Font(name="Segoe UI", size=10)

        worksheet.row_dimensions[1].height = 28
        for col_num in range(1, len(df.columns) + 1):
            cell = worksheet.cell(row=1, column=col_num)
            cell.font = font_header
            cell.alignment = align_header

        center_cols = list(range(1, len(df.columns) + 1))

        for row_num in range(2, len(df) + 2):
            worksheet.row_dimensions[row_num].height = 20

            for col_num in range(1, len(df.columns) + 1):
                cell = worksheet.cell(row=row_num, column=col_num)
                cell.font = font_body

                if col_num == 1:
                    cell.alignment = Alignment(horizontal="left", vertical="center")
                else:
                    cell.alignment = Alignment(horizontal="center", vertical="center")

        for col in worksheet.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)

            for cell in col:
                val = cell.value
                if val is not None:
                    val_len = len(str(val))
                    if val_len > max_len:
                        max_len = val_len

            worksheet.column_dimensions[col_letter].width = max(max_len + 3, 14)

    output.seek(0)

    filename = "reviewer_dashboard_report.xlsx"
    headers = {
        "Content-Disposition": f'attachment; filename=\"{filename}\"'
    }

    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers
    )


@router.get("/reviewers/filters")
def reviewer_dashboard_filters(conn=Depends(get_db)):
    stage_query = """
        SELECT DISTINCT name
        FROM stage
        WHERE name IS NOT NULL AND TRIM(name) <> ''
        ORDER BY name
    """

    state_query = """
        SELECT DISTINCT UPPER(TRIM(state)) AS state
        FROM sale_property
        WHERE state IS NOT NULL AND TRIM(state) <> ''
        ORDER BY state
    """

    status_query = """
        SELECT DISTINCT status
        FROM sale
        WHERE status IS NOT NULL AND TRIM(status) <> ''
        ORDER BY status
    """

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

    type_of_sale_query = """
        SELECT DISTINCT dealtype
        FROM sale
        WHERE dealtype IS NOT NULL AND TRIM(dealtype) <> ''
        ORDER BY dealtype
    """

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(stage_query)
        stage_list = [row["name"] for row in cur.fetchall()]

        cur.execute(state_query)
        state_list = [row["state"] for row in cur.fetchall()]

        cur.execute(status_query)
        status_list = [row["status"] for row in cur.fetchall()]

        cur.execute(reviewer_query)
        reviewer_list = [row["reviewer_name"] for row in cur.fetchall()]

        cur.execute(type_of_sale_query)
        type_of_sale_list = [row["dealtype"] for row in cur.fetchall()]

    return {
        "stage_list": stage_list,
        "state_list": state_list,
        "status_list": status_list,
        "reviewer_list": reviewer_list,
        "type_of_sale_list": type_of_sale_list
    }