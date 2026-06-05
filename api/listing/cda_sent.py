from fastapi import APIRouter, Query, Response, Depends
from db import get_conn, get_db
from psycopg2.extras import RealDictCursor
from services.comparison import compare_names, compare_listing_price
import io
import pandas as pd
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from decimal import Decimal
import datetime

router = APIRouter()

def fetch_cda_sent_data(filter: str, conn=None):
    passed_conn = conn is not None
    if not passed_conn:
        conn = get_conn()
    try:
        # For no_skyslope filter, run a simpler query and return early
        if filter == "no_skyslope":
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        be.transaction_identifier_transactionid AS transaction_id,
                        be.skyslopefileid,
                        be.property_address,
                        be.tags,
                        be.sale_price AS be_sale_price,
                        be.closed_date AS be_closed_date,
                        be.contract_date AS be_contract_date,
                        be.listing_price AS be_listing_price,
                        be.transaction_status AS be_transaction_status,
                        be.buyer_name AS be_buyer_name,
                        be.seller_name AS be_seller_name
                    FROM brokerage_engine be
                    WHERE be.tags LIKE '%CdaSent%'
                    AND be.skyslopefileid IS NULL
                    ORDER BY be.transaction_identifier_transactionid;
                """)
                no_skyslope_rows = cur.fetchall()
                cur.execute("""
                    SELECT COUNT(*) AS total_cda_sent
                    FROM brokerage_engine be
                    WHERE be.tags LIKE '%CdaSent%'
                """)
                summary = cur.fetchone()
                cur.execute("""
                    SELECT COUNT(*) AS no_skyslope_record
                    FROM brokerage_engine be
                    WHERE be.tags LIKE '%CdaSent%'
                    AND be.skyslopefileid IS NULL
                """)
                no_skyslope = cur.fetchone()
            return {
                "filter": filter,
                "total_cda_sent": summary["total_cda_sent"],
                "unmatched_count": None,
                "no_skyslope_record": no_skyslope["no_skyslope_record"],
                "data": [dict(row) for row in no_skyslope_rows],
            }
        # all / mismatch filters
        query = """
            WITH base AS (
                SELECT
                    be.transaction_identifier_transactionid AS transaction_id,
                    be.skyslopefileid,
                    be.property_address,
                    be.tags,
                    be.sale_price AS be_sale_price,
                    s.saleprice AS ss_sale_price,
                    be.closed_date AS be_closed_date,
                    s.escrowclosingdate AS ss_closed_date,
                    be.contract_date AS be_contract_date,
                    s.contractacceptancedate AS ss_contract_date,
                    be.listing_price AS be_listing_price,
                    s.listingprice AS ss_listing_price,
                    be.transaction_status AS be_transaction_status,
                    s.status AS ss_transaction_status,
                    CASE
                        WHEN be.tags ILIKE '%%listingside%%' AND be.tags ILIKE '%%sellingside%%'
                            THEN be.total_gross_commission
                        WHEN be.tags ILIKE '%%listingside%%'
                            THEN be.listing_side_gross_commission
                        WHEN be.tags ILIKE '%%sellingside%%'
                            THEN be.buying_side_gross_commission
                        ELSE be.buying_side_gross_commission
                    END AS be_gross_commission,
                    CASE
                        WHEN be.tags ILIKE '%%listingside%%' AND be.tags ILIKE '%%sellingside%%'
                            THEN scn.officegrosscommissiononsale
                        WHEN be.tags ILIKE '%%listingside%%'
                            THEN COALESCE(scn.listingcommissionamount, scn.officegrosscommissiononsale)
                        WHEN be.tags ILIKE '%%sellingside%%'
                            THEN COALESCE(scn.salecommissionamount, scn.officegrosscommissiononsale)
                        ELSE COALESCE(scn.salecommissionamount, scn.officegrosscommissiononsale)
                    END AS ss_gross_commission,
                    scn.officegrosscommissiononsale,
                    scn.listingcommissionamount,
                    scn.salecommissionamount,
                    COALESCE(
                        (
                            SELECT STRING_AGG(
                                TRIM(COALESCE(sc.firstname, '') || ' ' || COALESCE(sc.lastname, '')),
                                ', '
                            )
                            FROM sale_contact sc
                            WHERE sc.saleguid = s.saleguid
                            AND LOWER(sc.role) = 'buyer'
                        ),
                        ''
                    ) AS ss_buyer_name,
                    be.buyer_name AS be_buyer_name,
                    COALESCE(
                        (
                            SELECT STRING_AGG(
                                TRIM(COALESCE(sc.firstname, '') || ' ' || COALESCE(sc.lastname, '')),
                                ', '
                            )
                            FROM sale_contact sc
                            WHERE sc.saleguid = s.saleguid
                            AND LOWER(sc.role) = 'seller'
                        ),
                        ''
                    ) AS ss_seller_name,
                    be.seller_name AS be_seller_name,
                    CASE
                        WHEN be.sale_price IS DISTINCT FROM s.saleprice
                        THEN true
                        ELSE false
                    END AS sale_price_mismatch,
                    CASE
                        WHEN be.closed_date IS DISTINCT FROM s.escrowclosingdate
                        THEN true
                        ELSE false
                    END AS closed_date_mismatch,
                    CASE
                        WHEN be.contract_date IS DISTINCT FROM s.contractacceptancedate
                        THEN true
                        ELSE false
                    END AS contract_date_mismatch,
                    CASE
                        WHEN LOWER(s.status) = 'expired' THEN NULL
                        WHEN be.transaction_status IS NULL OR s.status IS NULL THEN NULL
                        WHEN LOWER(be.transaction_status) = LOWER(s.status) THEN false
                        WHEN LOWER(be.transaction_status) = 'cancelled'
                            AND LOWER(s.status) IN ('canceled/app', 'canceled/pend')
                        THEN false
                        ELSE true
                    END AS transaction_status_mismatch,
                    CASE
                        -- Both sides: compare against officegrosscommissiononsale
                        WHEN be.tags ILIKE '%%listingside%%' AND be.tags ILIKE '%%sellingside%%'
                            THEN CASE
                                WHEN scn.officegrosscommissiononsale IS NULL
                                OR be.total_gross_commission IS NULL
                                OR scn.officegrosscommissiononsale = 0
                                OR be.total_gross_commission = 0
                                THEN NULL
                                WHEN scn.officegrosscommissiononsale <> be.total_gross_commission
                                THEN 'mismatch'
                                ELSE 'match'
                            END

                        -- Listing side only: listingcommissionamount, fallback to officegrosscommissiononsale
                        WHEN be.tags ILIKE '%%listingside%%'
                            THEN CASE
                                WHEN COALESCE(scn.listingcommissionamount, scn.officegrosscommissiononsale) IS NULL
                                OR be.listing_side_gross_commission IS NULL
                                OR COALESCE(scn.listingcommissionamount, scn.officegrosscommissiononsale) = 0
                                OR be.listing_side_gross_commission = 0
                                THEN NULL
                                WHEN COALESCE(scn.listingcommissionamount, scn.officegrosscommissiononsale)
                                    <> be.listing_side_gross_commission
                                THEN 'mismatch'
                                ELSE 'match'
                            END

                        -- Selling side only (or fallback): salecommissionamount, fallback to officegrosscommissiononsale
                        ELSE
                            CASE
                                WHEN COALESCE(scn.salecommissionamount, scn.officegrosscommissiononsale) IS NULL
                                OR be.buying_side_gross_commission IS NULL
                                OR COALESCE(scn.salecommissionamount, scn.officegrosscommissiononsale) = 0
                                OR be.buying_side_gross_commission = 0
                                THEN NULL
                                WHEN COALESCE(scn.salecommissionamount, scn.officegrosscommissiononsale)
                                    <> be.buying_side_gross_commission
                                THEN 'mismatch'
                                ELSE 'match'
                            END
                    END AS gross_commission_mismatch
                FROM brokerage_engine be
                LEFT JOIN sale s ON s.saleguid = be.skyslopefileid
                LEFT JOIN sale_commission scn ON scn.saleguid = s.saleguid
                WHERE be.tags LIKE '%CdaSent%'
            )
            SELECT *
            FROM base
            ORDER BY transaction_id;
        """
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()
            reshaped_rows = []
            for row in rows:
                buyer_result = compare_names(
                    row["ss_buyer_name"],
                    row["be_buyer_name"]
                )
                seller_result = compare_names(
                    row["ss_seller_name"],
                    row["be_seller_name"]
                )
                listing_price_result = compare_listing_price(
                    row["be_listing_price"],
                    row["ss_listing_price"]
                )
                status_mismatch = row["transaction_status_mismatch"]
                gross_commission_mismatch = row["gross_commission_mismatch"]
                is_stale = (
                    row["sale_price_mismatch"]
                    or row["closed_date_mismatch"]
                    or row["contract_date_mismatch"]
                    or listing_price_result == "mismatch"
                    or status_mismatch is True
                    or buyer_result == "mismatch"
                    or seller_result == "mismatch"
                    or gross_commission_mismatch == "mismatch"
                )
                reshaped_row = {
                    "transaction_id": row["transaction_id"],
                    "skyslopefileid": row["skyslopefileid"],
                    "property_address": row["property_address"],
                    "tags": row["tags"],
                    "is_stale": is_stale,
                    "be_gross_commission": row["be_gross_commission"],
                    "ss_gross_commission": row["ss_gross_commission"],
                    "gross_commission_mismatch": gross_commission_mismatch,
                    "be_closed_date": row["be_closed_date"],
                    "ss_closed_date": row["ss_closed_date"],
                    "closed_date_mismatch": row["closed_date_mismatch"],
                    "be_sale_price": row["be_sale_price"],
                    "ss_sale_price": row["ss_sale_price"],
                    "sale_price_mismatch": row["sale_price_mismatch"],
                    "be_transaction_status": row["be_transaction_status"],
                    "ss_transaction_status": row["ss_transaction_status"],
                    "transaction_status_mismatch": status_mismatch,
                    "be_contract_date": row["be_contract_date"],
                    "ss_contract_date": row["ss_contract_date"],
                    "contract_date_mismatch": row["contract_date_mismatch"],
                    "be_listing_price": row["be_listing_price"],
                    "ss_listing_price": row["ss_listing_price"],
                    "listing_price_mismatch": listing_price_result,
                    "be_buyer_name": row["be_buyer_name"],
                    "ss_buyer_name": row["ss_buyer_name"],
                    "buyer_name_comparison": buyer_result,
                    "be_seller_name": row["be_seller_name"],
                    "ss_seller_name": row["ss_seller_name"],
                    "seller_name_comparison": seller_result,
                }
                if filter == "all":
                    reshaped_rows.append(reshaped_row)
                elif filter == "mismatch" and is_stale:
                    reshaped_rows.append(reshaped_row)
            cur.execute("""
                SELECT COUNT(*) AS total_cda_sent
                FROM brokerage_engine be
                LEFT JOIN sale s ON s.saleguid = be.skyslopefileid
                WHERE be.tags LIKE '%CdaSent%'
            """)
            summary = cur.fetchone()
            cur.execute("""
                SELECT COUNT(*) AS no_skyslope_record
                FROM brokerage_engine be
                WHERE be.tags LIKE '%CdaSent%'
                AND be.skyslopefileid IS NULL
            """)
            no_skyslope = cur.fetchone()
        stale_count = sum(1 for row in reshaped_rows if row["is_stale"])
        return {
            "filter": filter,
            "total_cda_sent": summary["total_cda_sent"],
            "unmatched_count": stale_count,
            "no_skyslope_record": no_skyslope["no_skyslope_record"],
            "data": reshaped_rows,
        }
    finally:
        if not passed_conn:
            conn.close()

@router.get("/cda-sent/listing")
def get_cda_sent(
    filter: str = Query("all", enum=["all", "mismatch", "no_skyslope"]),
    conn=Depends(get_db)
):
    return fetch_cda_sent_data(filter, conn)

@router.get("/cda-sent/download")
def download_cda_sent(
    filter: str = Query("all", enum=["all", "mismatch", "no_skyslope"]),
    conn=Depends(get_db)
):
    result = fetch_cda_sent_data(filter, conn)
    data = result["data"]

    if filter == "no_skyslope":
        columns_map = {
            "transaction_id": "Transaction ID",
            "skyslopefileid": "SkySlope File ID",
            "property_address": "Property Address",
            "tags": "Tags",
            "be_sale_price": "BE Sale Price",
            "be_closed_date": "BE Closed Date",
            "be_contract_date": "BE Contract Date",
            "be_listing_price": "BE Listing Price",
            "be_transaction_status": "BE Transaction Status",
            "be_buyer_name": "BE Buyer Name",
            "be_seller_name": "BE Seller Name"
        }
    else:
        columns_map = {
            "transaction_id": "Transaction ID",
            "skyslopefileid": "SkySlope File ID",
            "property_address": "Property Address",
            "tags": "Tags",
            "is_stale": "Is Stale",
            "be_gross_commission": "BE Gross Commission",
            "ss_gross_commission": "SS Gross Commission",
            "gross_commission_mismatch": "Gross Commission Mismatch",
            "be_closed_date": "BE Closed Date",
            "ss_closed_date": "SS Closed Date",
            "closed_date_mismatch": "Closed Date Mismatch",
            "be_sale_price": "BE Sale Price",
            "ss_sale_price": "SS Sale Price",
            "sale_price_mismatch": "Sale Price Mismatch",
            "be_transaction_status": "BE Transaction Status",
            "ss_transaction_status": "SS Transaction Status",
            "transaction_status_mismatch": "Transaction Status Mismatch",
            "be_contract_date": "BE Contract Date",
            "ss_contract_date": "SS Contract Date",
            "contract_date_mismatch": "Contract Date Mismatch",
            "be_listing_price": "BE Listing Price",
            "ss_listing_price": "SS Listing Price",
            "listing_price_mismatch": "Listing Price Mismatch",
            "be_buyer_name": "BE Buyer Name",
            "ss_buyer_name": "SS Buyer Name",
            "buyer_name_comparison": "Buyer Name Comparison",
            "be_seller_name": "BE Seller Name",
            "ss_seller_name": "SS Seller Name",
            "seller_name_comparison": "Seller Name Comparison"
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
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name="CDA Sent Report", index=False)
        
        workbook = writer.book
        worksheet = writer.sheets["CDA Sent Report"]
        
        # Explicitly show grid lines
        worksheet.views.sheetView[0].showGridLines = True
        
        # Premium styling definitions
        font_header = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
        fill_header = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
        align_header = Alignment(horizontal="center", vertical="center", wrap_text=True)
        
        font_body = Font(name="Segoe UI", size=10)
        fill_even = PatternFill(start_color="F9FAFB", end_color="F9FAFB", fill_type="solid") # subtle gray zebra striping
        fill_mismatch = PatternFill(start_color="FCE8E6", end_color="FCE8E6", fill_type="solid") # soft pastel red
        font_mismatch = Font(name="Segoe UI", size=10, bold=True, color="C53929") # clear dark red warning text
        
        thin_border = Border(
            left=Side(style='thin', color='D0D5DD'),
            right=Side(style='thin', color='D0D5DD'),
            top=Side(style='thin', color='D0D5DD'),
            bottom=Side(style='thin', color='D0D5DD')
        )
        
        # Apply header styling
        worksheet.row_dimensions[1].height = 28
        for col_num in range(1, len(df.columns) + 1):
            cell = worksheet.cell(row=1, column=col_num)
            cell.font = font_header
            cell.fill = fill_header
            cell.alignment = align_header
            cell.border = thin_border
            
        # Classify columns for appropriate aligning/formatting
        currency_cols = []
        date_cols = []
        center_cols = []
        
        currency_keywords = ["gross commission", "sale price", "listing price"]
        date_keywords = ["closed date", "contract date"]
        center_keywords = ["id", "is stale", "mismatch", "comparison", "tags"]
        
        for idx, col_name in enumerate(df.columns):
            col_name_lower = col_name.lower()
            if any(kw in col_name_lower for kw in currency_keywords):
                currency_cols.append(idx + 1)
            elif any(kw in col_name_lower for kw in date_keywords):
                date_cols.append(idx + 1)
            elif any(kw in col_name_lower for kw in center_keywords):
                center_cols.append(idx + 1)
                
        # Style body rows
        for row_num in range(2, len(df) + 2):
            worksheet.row_dimensions[row_num].height = 20
            is_even_row = (row_num % 2 == 0)
            
            for col_num in range(1, len(df.columns) + 1):
                cell = worksheet.cell(row=row_num, column=col_num)
                cell.font = font_body
                cell.border = thin_border
                
                # Apply Zebra striping as base fill
                if is_even_row:
                    cell.fill = fill_even
                
                val = cell.value
                val_str = str(val).strip().lower() if val is not None else ""
                col_name = df.columns[col_num - 1]
                col_name_lower = col_name.lower()
                
                # Highlight mismatches
                is_cell_mismatch = False
                if "mismatch" in col_name_lower or "comparison" in col_name_lower:
                    if val_str in ["yes", "mismatch"]:
                        is_cell_mismatch = True
                elif col_name_lower == "is stale":
                    if val_str == "yes":
                        is_cell_mismatch = True
                        
                if is_cell_mismatch:
                    cell.fill = fill_mismatch
                    cell.font = font_mismatch
                
                # Apply alignment and number formats
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
                    
        # Adjust column widths dynamically to prevent clipping
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
    
    filename = f"cda_sent_report_{filter}.xlsx"
    headers = {
        'Content-Disposition': f'attachment; filename="{filename}"'
    }
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers
    )
