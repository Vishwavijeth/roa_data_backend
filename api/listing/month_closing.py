from fastapi import APIRouter, Query, Response
from typing import List
from db import get_conn
from psycopg2.extras import RealDictCursor
from services.comparison import compare_names, compare_listing_price
import io
import pandas as pd
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from decimal import Decimal
import datetime

router = APIRouter()

def fetch_month_closing_data(
    status: str = "all",
    skyslope: bool = False,
    state: List[str] = [],
    from_close_date: str = None,
    to_close_date: str = None,
    transaction_specialist: List[str] = [],
    search: str = None,
    mismatch: bool = False,
    pending_subfilter: List[str] = [],
    page: int = None,
    page_size: int = None,
):
    conn = get_conn()
    try:
        search_clause = ""
        search_params = {}

        # ── parse multi-value params (supports both ?x=A,B and ?x=A&x=B) ──
        state_list = [v.strip() for s in state             for v in s.split(",") if v.strip()]
        ts_list    = [v.strip() for s in transaction_specialist for v in s.split(",") if v.strip()]
        ps_list    = [v.strip() for s in pending_subfilter  for v in s.split(",") if v.strip()]
        # ──────────────────────────────────────────────────────────────────

        # ---------------- SEARCH FILTER ----------------
        if search:
            search_clause = """
                AND (
                    COALESCE(s.saleguid::text, '') ILIKE %(search)s
                    OR COALESCE(sp.streetaddress, '') ILIKE %(search)s
                    OR COALESCE(sp.county, '') ILIKE %(search)s
                    OR COALESCE(sp.state, '') ILIKE %(search)s
                    OR COALESCE(sp.zip, '') ILIKE %(search)s
                    OR COALESCE(r.firstname, '') ILIKE %(search)s
                    OR COALESCE(r.lastname, '') ILIKE %(search)s
                )
            """
            search_params["search"] = f"%{search}%"

        # ─────────────────────────────────────────────
        # SKY SLOPE MODE
        # ─────────────────────────────────────────────
        if skyslope:
            shared_filters = ""
            params = {}

            if state_list:
                placeholders = ", ".join(f"%(state_{i})s" for i in range(len(state_list)))
                shared_filters += f" AND LOWER(sp.state) IN ({placeholders})"
                for i, v in enumerate(state_list):
                    params[f"state_{i}"] = v.lower()

            if from_close_date:
                shared_filters += " AND s.escrowclosingdate >= %(from_close_date)s"
                params["from_close_date"] = from_close_date
            if to_close_date:
                shared_filters += " AND s.escrowclosingdate <= %(to_close_date)s"
                params["to_close_date"] = to_close_date

            base_from = """
                FROM sale s
                LEFT JOIN brokerage_engine be ON be.skyslopefileid = s.saleguid
                LEFT JOIN users r             ON s.reviewerguid = r.userguid
                LEFT JOIN sale_property sp    ON sp.saleguid = s.saleguid
                WHERE be.skyslopefileid IS NULL
                AND LOWER(TRIM(COALESCE(s.status, ''))) NOT IN ('canceled/app', 'canceled/pend')
            """
            base_from += search_clause

            count_query = "SELECT COUNT(*) AS total " + base_from + shared_filters + ";"
            data_query = """
                SELECT
                    s.saleguid AS skyslopefileid,
                    s.saleprice AS ss_sale_price,
                    s.status AS ss_status,
                    s.escrowclosingdate AS ss_closed_date,
                    s.contractacceptancedate AS ss_contract_date,
                    s.listingprice AS ss_listing_price,
                    sp.state AS state,
                    CONCAT_WS(', ',
                        CONCAT_WS(' ',
                            sp.streetnumber,
                            sp.streetaddress,
                            sp.unit,
                            sp.direction
                        ),
                        sp.county,
                        sp.state,
                        sp.zip
                    ) AS property_address,
                    COALESCE(r.firstname || ' ' || r.lastname, '') AS reviewer
            """ + base_from + shared_filters

            params.update(search_params)

            if page is not None and page_size is not None:
                offset = (page - 1) * page_size
                data_query += """
                    ORDER BY s.saleguid
                    LIMIT %(limit)s OFFSET %(offset)s;
                """
                params["limit"] = page_size
                params["offset"] = offset
            else:
                data_query += " ORDER BY s.saleguid;"

            count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}

            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(count_query, count_params)
                total = cur.fetchone()["total"]
                cur.execute(data_query, params)
                rows = cur.fetchall()

            return {"mode": "skyslope_only", "total": total, "data": [dict(r) for r in rows]}

        # ─────────────────────────────────────────────
        # FULL COMPARISON MODE
        # ─────────────────────────────────────────────
        base_cte = """
            WITH base AS (
                SELECT
                    be.transaction_identifier_transactionid AS transaction_id,
                    be.skyslopefileid,
                    be.property_address,
                    be.state,
                    be.transaction_specialist,
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
                    be.buyer_name,
                    be.seller_name,
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
                        WHEN be.tags ILIKE '%%titlepaymentreceived%%' THEN 'titlepaymentreceived'
                        WHEN be.tags ILIKE '%%commissionverified%%'   THEN 'commissionverified'
                        WHEN be.tags ILIKE '%%cdasent%%'              THEN 'cdasent'
                        WHEN be.tags ILIKE '%%complete%%'             THEN 'complete'
                        WHEN be.tags ILIKE '%%open%%'                 THEN 'open'
                        ELSE NULL
                    END AS be_stage,
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
                    CASE
                        WHEN be.sale_price IS NULL OR s.saleprice IS NULL THEN NULL
                        WHEN be.sale_price IS DISTINCT FROM s.saleprice   THEN 'mismatch'
                        ELSE 'match'
                    END AS sale_price_comparison,
                    CASE
                        WHEN be.closed_date IS NULL OR s.escrowclosingdate IS NULL THEN NULL
                        WHEN be.closed_date IS DISTINCT FROM s.escrowclosingdate   THEN 'mismatch'
                        ELSE 'match'
                    END AS closed_date_comparison,
                    CASE
                        WHEN be.contract_date IS NULL OR s.contractacceptancedate IS NULL THEN NULL
                        WHEN be.contract_date IS DISTINCT FROM s.contractacceptancedate   THEN 'mismatch'
                        ELSE 'match'
                    END AS contract_date_comparison,
                    CASE
                        WHEN be.transaction_status IS NULL OR TRIM(be.transaction_status) = ''
                        OR s.status IS NULL            OR TRIM(s.status) = ''
                        THEN NULL
                        WHEN LOWER(s.status) = 'expired' THEN NULL
                        WHEN LOWER(be.transaction_status) = 'closed'
                            AND LOWER(s.status) = 'archived'
                        THEN 'match'
                        WHEN LOWER(be.transaction_status) = LOWER(s.status) THEN 'match'
                        WHEN LOWER(be.transaction_status) = 'cancelled'
                            AND LOWER(s.status) IN ('canceled/app', 'canceled/pend')
                        THEN 'match'
                        ELSE 'mismatch'
                    END AS transaction_status_comparison,
                    CASE
                        -- Both sides: compare be.total_gross_commission vs officegrosscommissiononsale
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
            )
        """

        where_clause = " WHERE 1=1"
        params = {}

        # ---------------- STATUS FILTER ----------------
        if status != "all":
            where_clause += """
                AND (
                    CASE
                        WHEN be_transaction_status ILIKE 'pending'
                          OR be_transaction_status ILIKE 'active'
                          OR be_transaction_status ILIKE 'in_progress'
                        THEN 'pending'
                        WHEN be_transaction_status ILIKE 'closed'
                        THEN 'closed'
                        WHEN be_transaction_status ILIKE 'cancelled'
                          OR be_transaction_status ILIKE 'canceled'
                          OR be_transaction_status ILIKE 'canceled/app'
                          OR be_transaction_status ILIKE 'canceled/pend'
                        THEN 'cancelled'
                        ELSE 'other'
                    END = %(status)s
                )
            """
            params["status"] = status

        # ---------------- PENDING SUB-FILTER (multi) ----------------
        if ps_list:
            placeholders = ", ".join(f"%(ps_{i})s" for i in range(len(ps_list)))
            where_clause += f" AND b.be_stage IN ({placeholders})"
            for i, v in enumerate(ps_list):
                params[f"ps_{i}"] = v

        # ---------------- STATE FILTER (multi) ----------------
        if state_list:
            placeholders = ", ".join(f"%(state_{i})s" for i in range(len(state_list)))
            where_clause += f" AND LOWER(b.state) IN ({placeholders})"
            for i, v in enumerate(state_list):
                params[f"state_{i}"] = v.lower()

        # ---------------- TRANSACTION SPECIALIST FILTER (multi) ----------------
        if ts_list:
            unassigned_requested = any(v.lower() == "unassigned" for v in ts_list)
            named = [v for v in ts_list if v.lower() != "unassigned"]

            if unassigned_requested and named:
                placeholders = ", ".join(f"%(ts_{i})s" for i in range(len(named)))
                where_clause += f"""
                    AND (
                        b.transaction_specialist IS NULL
                        OR b.transaction_specialist = ''
                        OR b.transaction_specialist IN ({placeholders})
                    )
                """
                for i, v in enumerate(named):
                    params[f"ts_{i}"] = v
            elif unassigned_requested:
                where_clause += """
                    AND (
                        b.transaction_specialist IS NULL
                        OR b.transaction_specialist = ''
                    )
                """
            else:
                placeholders = ", ".join(f"%(ts_{i})s" for i in range(len(named)))
                where_clause += f" AND b.transaction_specialist IN ({placeholders})"
                for i, v in enumerate(named):
                    params[f"ts_{i}"] = v

        # ---------------- DATE FILTER ----------------
        if from_close_date:
            where_clause += " AND b.be_closed_date >= %(from_close_date)s"
            params["from_close_date"] = from_close_date
        if to_close_date:
            where_clause += " AND b.be_closed_date <= %(to_close_date)s"
            params["to_close_date"] = to_close_date

        # ---------------- SEARCH FILTER ----------------
        if search:
            where_clause += """
                AND (
                    COALESCE(b.transaction_id::text, '') ILIKE %(search)s
                    OR COALESCE(b.property_address, '') ILIKE %(search)s
                    OR COALESCE(b.state, '') ILIKE %(search)s
                    OR COALESCE(b.transaction_specialist, '') ILIKE %(search)s
                    OR COALESCE(b.buyer_name, '') ILIKE %(search)s
                    OR COALESCE(b.seller_name, '') ILIKE %(search)s
                    OR COALESCE(b.skyslopefileid::text, '') ILIKE %(search)s
                )
            """
            params["search"] = f"%{search}%"

        count_query = base_cte + " SELECT COUNT(*) AS total FROM base b" + where_clause + ";"
        data_query  = (
            base_cte
            + " SELECT * FROM base b"
            + where_clause
            + " ORDER BY b.transaction_id"
        )
        
        if page is not None and page_size is not None:
            offset = (page - 1) * page_size
            data_query += " LIMIT %(limit)s OFFSET %(offset)s;"
            params["limit"]  = page_size
            params["offset"] = offset
        else:
            data_query += ";"
            
        count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(count_query, count_params)
            total = cur.fetchone()["total"]
            cur.execute(data_query, params)
            rows = cur.fetchall()

        # ---------------- POST PROCESSING ----------------
        for row in rows:
            row["buyer_name_comparison"]    = compare_names(row.get("buyer_name"),       row.get("ss_buyer_name"))
            row["seller_name_comparison"]   = compare_names(row.get("seller_name"),      row.get("ss_seller_name"))
            row["listing_price_comparison"] = compare_listing_price(row.get("be_listing_price"), row.get("ss_listing_price"))

        # ---------------- MISMATCH FILTER ----------------
        if mismatch:
            def has_mismatch(r):
                return any(
                    r.get(k) == "mismatch" for k in (
                        "sale_price_comparison", "closed_date_comparison",
                        "contract_date_comparison", "transaction_status_comparison",
                        "gross_commission_mismatch", "gross_commission_comparison",
                        "buyer_name_comparison", "seller_name_comparison", "listing_price_comparison",
                    )
                )
            rows  = [r for r in rows if has_mismatch(r)]
            total = len(rows)

        return {"mode": "full_comparison", "total": total, "data": [dict(r) for r in rows]}

    finally:
        conn.close()

@router.get("/month-closing/listing")
def get_month_closing(
    status: str = "all",
    skyslope: bool = False,
    state: List[str] = Query(default=[]),
    from_close_date: str = None,
    to_close_date: str = None,
    transaction_specialist: List[str] = Query(default=[]),
    search: str = None,
    mismatch: bool = False,
    pending_subfilter: List[str] = Query(default=[]),
    page: int = 1,
    page_size: int = 50,
):
    return fetch_month_closing_data(
        status=status,
        skyslope=skyslope,
        state=state,
        from_close_date=from_close_date,
        to_close_date=to_close_date,
        transaction_specialist=transaction_specialist,
        search=search,
        mismatch=mismatch,
        pending_subfilter=pending_subfilter,
        page=page,
        page_size=page_size,
    )

@router.get("/month-closing/download")
def download_month_closing(
    status: str = "all",
    skyslope: bool = False,
    state: List[str] = Query(default=[]),
    from_close_date: str = None,
    to_close_date: str = None,
    transaction_specialist: List[str] = Query(default=[]),
    search: str = None,
    mismatch: bool = False,
    pending_subfilter: List[str] = Query(default=[]),
):
    result = fetch_month_closing_data(
        status=status,
        skyslope=skyslope,
        state=state,
        from_close_date=from_close_date,
        to_close_date=to_close_date,
        transaction_specialist=transaction_specialist,
        search=search,
        mismatch=mismatch,
        pending_subfilter=pending_subfilter,
        page=None,
        page_size=None,
    )
    
    data = result["data"]
    
    if skyslope:
        columns_map = {
            "skyslopefileid": "SkySlope File ID",
            "ss_sale_price": "SS Sale Price",
            "ss_status": "SS Status",
            "ss_closed_date": "SS Closed Date",
            "ss_contract_date": "SS Contract Date",
            "ss_listing_price": "SS Listing Price",
            "state": "State",
            "property_address": "Property Address",
            "reviewer": "Reviewer"
        }
    else:
        columns_map = {
            "transaction_id": "Transaction ID",
            "skyslopefileid": "SkySlope File ID",
            "property_address": "Property Address",
            "state": "State",
            "transaction_specialist": "Transaction Specialist",
            "be_stage": "BE Stage",
            "be_sale_price": "BE Sale Price",
            "ss_sale_price": "SS Sale Price",
            "sale_price_comparison": "Sale Price Comparison",
            "be_closed_date": "BE Closed Date",
            "ss_closed_date": "SS Closed Date",
            "closed_date_comparison": "Closed Date Comparison",
            "be_contract_date": "BE Contract Date",
            "ss_contract_date": "SS Contract Date",
            "contract_date_comparison": "Contract Date Comparison",
            "be_listing_price": "BE Listing Price",
            "ss_listing_price": "SS Listing Price",
            "listing_price_comparison": "Listing Price Comparison",
            "be_transaction_status": "BE Transaction Status",
            "ss_transaction_status": "SS Transaction Status",
            "transaction_status_comparison": "Transaction Status Comparison",
            "be_gross_commission": "BE Gross Commission",
            "ss_gross_commission": "SS Gross Commission",
            "gross_commission_mismatch": "Gross Commission Mismatch",
            "buyer_name": "BE Buyer Name",
            "ss_buyer_name": "SS Buyer Name",
            "buyer_name_comparison": "Buyer Name Comparison",
            "seller_name": "BE Seller Name",
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
        df.to_excel(writer, sheet_name="Month Closing Report", index=False)
        
        workbook = writer.book
        worksheet = writer.sheets["Month Closing Report"]
        
        worksheet.views.sheetView[0].showGridLines = True
        
        font_header = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
        fill_header = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
        align_header = Alignment(horizontal="center", vertical="center", wrap_text=True)
        
        font_body = Font(name="Segoe UI", size=10)
        fill_even = PatternFill(start_color="F9FAFB", end_color="F9FAFB", fill_type="solid")
        fill_mismatch = PatternFill(start_color="FCE8E6", end_color="FCE8E6", fill_type="solid")
        font_mismatch = Font(name="Segoe UI", size=10, bold=True, color="C53929")
        
        thin_border = Border(
            left=Side(style='thin', color='D0D5DD'),
            right=Side(style='thin', color='D0D5DD'),
            top=Side(style='thin', color='D0D5DD'),
            bottom=Side(style='thin', color='D0D5DD')
        )
        
        worksheet.row_dimensions[1].height = 28
        for col_num in range(1, len(df.columns) + 1):
            cell = worksheet.cell(row=1, column=col_num)
            cell.font = font_header
            cell.fill = fill_header
            cell.alignment = align_header
            cell.border = thin_border
            
        currency_cols = []
        date_cols = []
        center_cols = []
        
        currency_keywords = ["gross commission", "sale price", "listing price"]
        date_keywords = ["closed date", "contract date"]
        center_keywords = ["id", "comparison", "mismatch", "state", "status", "stage"]
        
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
            is_even_row = (row_num % 2 == 0)
            
            for col_num in range(1, len(df.columns) + 1):
                cell = worksheet.cell(row=row_num, column=col_num)
                cell.font = font_body
                cell.border = thin_border
                
                if is_even_row:
                    cell.fill = fill_even
                
                val = cell.value
                val_str = str(val).strip().lower() if val is not None else ""
                col_name = df.columns[col_num - 1]
                col_name_lower = col_name.lower()
                
                # Mismatch coloring
                is_cell_mismatch = False
                if any(kw in col_name_lower for kw in ["comparison", "mismatch"]):
                    if val_str in ["yes", "mismatch"]:
                        is_cell_mismatch = True
                        
                if is_cell_mismatch:
                    cell.fill = fill_mismatch
                    cell.font = font_mismatch
                
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
    
    filename = f"month_closing_report_{status}.xlsx"
    headers = {
        'Content-Disposition': f'attachment; filename="{filename}"'
    }
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers
    )
