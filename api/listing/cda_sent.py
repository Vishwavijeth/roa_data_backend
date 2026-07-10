from fastapi import APIRouter, Query, Response, Depends
from db import get_db
from psycopg2.extras import RealDictCursor
import io
import pandas as pd
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from decimal import Decimal
import datetime

router = APIRouter()


def fetch_cda_sent_data(conn, mismatch: bool = False, search: str = None, page: int = None, limit: int = None):
    base_params = ["%CdaSent%", "%CdaSent%"]
    extra_params = []
    where_conditions = []

    base_cte = """
        WITH brokerage_base AS (
            SELECT
                be.transaction_identifier_transactionid AS transaction_id,
                be.tags
            FROM brokerage_engine be
            WHERE be.tags ILIKE %s
        ),
        other_income_base AS (
            SELECT
                oit.transaction_identifier_transactionid AS transaction_id,
                oit.tags
            FROM otherincome_transactions oit
            WHERE oit.tags ILIKE %s
        ),
        combined_source AS (
            SELECT * FROM brokerage_base
            UNION ALL
            SELECT * FROM other_income_base
        ),
        base AS (
            SELECT
                cs.transaction_id,
                cs.tags,
                rd.be_source_table,
                rd.property_address,
                rd.be_gross_commission,
                rd.skyslope_gross_commission,
                rd.gross_commission_match,
                rd.be_sale_price,
                rd.skyslope_sale_price,
                rd.sale_price_match,
                rd.be_close_date,
                rd.skyslope_close_date_value,
                rd.close_date_match,
                rd.be_status,
                rd.skyslope_status_value,
                rd.status_match
            FROM combined_source cs
            LEFT JOIN reconciliation_data rd
                ON rd.transactionid = cs.transaction_id
        )
    """

    mismatch_condition = """
        (
            gross_commission_match = 'mismatch'
            OR sale_price_match = 'mismatch'
            OR close_date_match = 'mismatch'
            OR status_match = 'mismatch'
        )
    """

    if mismatch:
        where_conditions.append(mismatch_condition)

    if search:
        where_conditions.append("""
            (
                CAST(transaction_id AS TEXT) ILIKE %s
                OR property_address ILIKE %s
                OR tags ILIKE %s
                OR be_source_table ILIKE %s
            )
        """)
        search_term = f"%{search}%"
        extra_params.extend([search_term, search_term, search_term, search_term])

    where_clause = ""
    if where_conditions:
        where_clause = "WHERE " + " AND ".join(where_conditions)

    summary_query = f"""
        {base_cte}
        SELECT
            COUNT(*) AS total_count,
            COUNT(*) FILTER (
                WHERE {mismatch_condition}
            ) AS mismatch_count
        FROM base;
    """

    filtered_count_query = f"""
        {base_cte}
        SELECT COUNT(*) AS count
        FROM base
        {where_clause};
    """

    data_query = f"""
        {base_cte}
        SELECT
            transaction_id,
            tags,
            be_source_table,
            property_address,
            be_gross_commission,
            skyslope_gross_commission,
            gross_commission_match,
            be_sale_price,
            skyslope_sale_price,
            sale_price_match,
            be_close_date,
            skyslope_close_date_value,
            close_date_match,
            be_status,
            skyslope_status_value,
            status_match
        FROM base
        {where_clause}
        ORDER BY transaction_id
    """

    summary_params = base_params
    filtered_count_params = base_params + extra_params
    data_params = base_params + extra_params

    if page is not None and limit is not None:
        offset = (page - 1) * limit
        data_query += " LIMIT %s OFFSET %s"
        data_params += [limit, offset]

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(summary_query, summary_params)
        summary = cur.fetchone()

        cur.execute(filtered_count_query, filtered_count_params)
        filtered_count = cur.fetchone()["count"]

        cur.execute(data_query, data_params)
        rows = cur.fetchall()

    return {
        "summary": {
            "total_count": summary["total_count"],
            "mismatch_count": summary["mismatch_count"]
        },
        "filtered_count": filtered_count,
        "rows": [dict(row) for row in rows]
    }


@router.get("/cda-sent")
def get_cda_sent(
    mismatch: bool = Query(False, description="If true, returns only mismatch records"),
    page: int = Query(default=1, ge=1),
    search: str = Query(default=None),
    conn=Depends(get_db)
):
    limit = 50
    result = fetch_cda_sent_data(
        conn=conn,
        mismatch=mismatch,
        search=search,
        page=page,
        limit=limit
    )

    total_pages = (result["filtered_count"] + limit - 1) // limit

    return {
        "mismatch": mismatch,
        "summary": result["summary"],
        "page": page,
        "page_size": limit,
        "total_pages": total_pages,
        "data": result["rows"],
    }


@router.get("/cda-sent/download")
def download_cda_sent(
    mismatch: bool = Query(False, description="If true, downloads only mismatch records"),
    search: str = Query(default=None),
    conn=Depends(get_db)
):
    result = fetch_cda_sent_data(
        conn=conn,
        mismatch=mismatch,
        search=search
    )

    data = result["rows"]

    columns_map = {
        "transaction_id": "Transaction ID",
        "tags": "Tags",
        "be_source_table": "BE Source Table",
        "property_address": "Property Address",
        "be_gross_commission": "BE Gross Commission",
        "skyslope_gross_commission": "SkySlope Gross Commission",
        "gross_commission_match": "Gross Commission Match",
        "be_sale_price": "BE Sale Price",
        "skyslope_sale_price": "SkySlope Sale Price",
        "sale_price_match": "Sale Price Match",
        "be_close_date": "BE Close Date",
        "skyslope_close_date_value": "SkySlope Close Date",
        "close_date_match": "Close Date Match",
        "be_status": "BE Status",
        "skyslope_status_value": "SkySlope Status",
        "status_match": "Status Match",
    }

    rows_to_export = []
    for row in data:
        export_row = {}
        for key, header in columns_map.items():
            value = row.get(key)

            if isinstance(value, Decimal):
                value = float(value)
            elif isinstance(value, (datetime.date, datetime.datetime)):
                value = value.strftime("%Y-%m-%d")
            elif isinstance(value, bool):
                value = "Yes" if value else "No"
            elif value is None:
                value = ""

            export_row[header] = value

        rows_to_export.append(export_row)

    df = pd.DataFrame(rows_to_export)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="CDA Sent Report", index=False)

        worksheet = writer.sheets["CDA Sent Report"]

        for cell in worksheet[1]:
            cell.font = Font(bold=True)

        for col in worksheet.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)

            for cell in col:
                value = cell.value
                if value is not None:
                    max_len = max(max_len, len(str(value)))

            worksheet.column_dimensions[col_letter].width = max(max_len + 2, 12)

    output.seek(0)

    file_suffix = "mismatch" if mismatch else "all"
    if search:
        file_suffix += "_filtered"

    filename = f"cda_sent_report_{file_suffix}.xlsx"

    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )