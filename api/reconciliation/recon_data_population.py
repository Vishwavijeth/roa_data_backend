from datetime import datetime
from fastapi import APIRouter, Depends
from psycopg2.extras import RealDictCursor, execute_values
from db import get_db
from services.recon_data_population import evaluate_row

router = APIRouter()

RECONCILIATION_DATA_BASE_QUERY = """
WITH brokerage_base AS (
    SELECT
        'sale income'::text AS be_source_table,
        be.skyslopefileid AS skyslopefileid,
        be.transaction_identifier_transactionid::uuid AS transactionid,
        be.property_address::varchar AS property_address,
        be.transaction_status::text AS be_status,
        be.tags::text AS tags,
        be.buyer_name::varchar AS be_buyer_name,
        be.seller_name::varchar AS be_seller_name,
        be.buying_agent_name::varchar AS be_buying_agent_name,
        be.da_title_company::varchar AS be_title_company,
        be.state::varchar AS be_state,
        be.sale_price::numeric AS be_sale_price,
        be.listing_price::numeric AS be_listing_price,
        be.closed_date::date AS be_close_date,
        be.contract_date::date AS be_contract_date,
        be.transaction_specialist::varchar AS be_transaction_specialist,
        CASE
            WHEN be.tags ILIKE '%%listingside%%' AND be.tags ILIKE '%%sellingside%%'
                THEN be.total_gross_commission
            WHEN be.tags ILIKE '%%listingside%%'
                THEN be.listing_side_gross_commission
            WHEN be.tags ILIKE '%%sellingside%%'
                THEN be.buying_side_gross_commission
            ELSE be.buying_side_gross_commission
        END::numeric AS be_gross_commission
    FROM brokerage_engine be
),
other_income_base AS (
    SELECT
        'other income'::text AS be_source_table,
        oit.skyslopefileid AS skyslopefileid,
        oit.transaction_identifier_transactionid::uuid AS transactionid,
        oit.property_address::varchar AS property_address,
        oit.transaction_status::text AS be_status,
        oit.tags::text AS tags,
        NULL::varchar AS be_buyer_name,
        NULL::varchar AS be_seller_name,
        NULL::varchar AS be_buying_agent_name,
        NULL::varchar AS be_title_company,
        NULL::varchar AS be_state,
        NULL::numeric AS be_sale_price,
        NULL::numeric AS be_listing_price,
        oit.income_received_date::date AS be_close_date,
        NULL::date AS be_contract_date,
        oit.transaction_specialist::varchar AS be_transaction_specialist,
        oit.gross_commission::numeric AS be_gross_commission
    FROM otherincome_transactions oit
),
combined_source AS (
    SELECT * FROM brokerage_base
    UNION ALL
    SELECT * FROM other_income_base
),
buyer_contacts AS (
    SELECT
        sc.saleguid,
        STRING_AGG(
            TRIM(COALESCE(sc.firstname, '') || ' ' || COALESCE(sc.lastname, '')),
            ', ' ORDER BY sc.firstname, sc.lastname
        ) AS skyslope_buyer_name
    FROM sale_contact sc
    WHERE LOWER(sc.role) = 'buyer'
    GROUP BY sc.saleguid
),
seller_contacts AS (
    SELECT
        sc.saleguid,
        STRING_AGG(
            TRIM(COALESCE(sc.firstname, '') || ' ' || COALESCE(sc.lastname, '')),
            ', ' ORDER BY sc.firstname, sc.lastname
        ) AS skyslope_seller_name
    FROM sale_contact sc
    WHERE LOWER(sc.role) = 'seller'
    GROUP BY sc.saleguid
),
title_companies AS (
    SELECT DISTINCT ON (sc.saleguid)
        sc.saleguid,
        sc.company::varchar AS skyslope_title_company
    FROM sale_contact sc
    WHERE LOWER(sc.role) = 'titlecompany'
    ORDER BY sc.saleguid, sc.company
),
agent_names AS (
    SELECT
        uu.userguid,
        TRIM(COALESCE(uu.firstname, '') || ' ' || COALESCE(uu.lastname, ''))::varchar AS skyslope_buying_agent_name
    FROM users uu
),
reviewer_names AS (
    SELECT
        uu.userguid,
        TRIM(COALESCE(uu.firstname, '') || ' ' || COALESCE(uu.lastname, ''))::varchar AS skyslope_reviewer
    FROM users uu
)
SELECT
    cs.transactionid,
    cs.be_source_table,
    s.saleguid,
    cs.property_address,

    cs.be_close_date,
    cs.be_status,
    cs.be_transaction_specialist,
    rn.skyslope_reviewer,
    cs.tags,

    cs.be_gross_commission,

    scn.officegrosscommissiononsale::numeric AS skyslope_gross_commission,

    cs.be_close_date AS be_close_date_value,
    s.escrowclosingdate::date AS skyslope_close_date,
    s.escrowclosingdate::date AS skyslope_close_date_value,

    cs.be_status AS be_status_value,
    s.status AS skyslope_status,
    s.status AS skyslope_status_value,

    cs.be_sale_price,
    s.saleprice::numeric AS skyslope_sale_price,

    cs.be_listing_price,
    s.listingprice::numeric AS skyslope_listing_price,

    cs.be_contract_date,
    s.contractacceptancedate::date AS skyslope_contract_date,

    cs.be_buyer_name,
    COALESCE(bc.skyslope_buyer_name, '')::varchar AS skyslope_buyer_name,

    cs.be_seller_name,
    COALESCE(sec.skyslope_seller_name, '')::varchar AS skyslope_seller_name,

    cs.be_buying_agent_name,
    COALESCE(an.skyslope_buying_agent_name, '')::varchar AS skyslope_buying_agent_name,

    cs.be_title_company,
    COALESCE(tc.skyslope_title_company, '')::varchar AS skyslope_title_company,

    scn.officegrosscommissiononsale::numeric AS officegrosscommissiononsale,
    scn.adminbrokeragecomp::numeric AS adminbrokeragecomp,
    scn.listingcommissionamount::numeric AS listingcommissionamount,
    scn.salecommissionamount::numeric AS salecommissionamount
FROM combined_source cs
LEFT JOIN sale s ON s.saleguid = cs.skyslopefileid
LEFT JOIN sale_commission scn ON scn.saleguid = s.saleguid
LEFT JOIN buyer_contacts bc ON bc.saleguid = s.saleguid
LEFT JOIN seller_contacts sec ON sec.saleguid = s.saleguid
LEFT JOIN title_companies tc ON tc.saleguid = s.saleguid
LEFT JOIN agent_names an ON an.userguid = s.agentguid
LEFT JOIN reviewer_names rn ON rn.userguid = s.reviewerguid
WHERE cs.transactionid IS NOT NULL
"""


INSERT_COLUMNS = [
    "transactionid",
    "be_source_table",
    "saleguid",
    "property_address",
    "be_close_date",
    "be_status",
    "be_transaction_specialist",
    "skyslope_reviewer",
    "be_gross_commission",
    "skyslope_gross_commission",
    "gross_commission_match",
    "be_close_date_value",
    "skyslope_close_date_value",
    "close_date_match",
    "be_status_value",
    "skyslope_status_value",
    "status_match",
    "be_sale_price",
    "skyslope_sale_price",
    "sale_price_match",
    "be_listing_price",
    "skyslope_listing_price",
    "listing_price_match",
    "be_contract_date",
    "skyslope_contract_date",
    "contract_date_match",
    "be_buyer_name",
    "skyslope_buyer_name",
    "buyer_name_match",
    "be_seller_name",
    "skyslope_seller_name",
    "seller_name_match",
    "be_buying_agent_name",
    "skyslope_buying_agent_name",
    "buying_agent_match",
    "be_title_company",
    "skyslope_title_company",
    "title_company_match",
    "evaluated_at",
]

EXPECTED_COLS = len(INSERT_COLUMNS)


def get_contract_date_match(row: dict):
    saleguid = row.get("saleguid")
    be_status = row.get("be_status")
    skyslope_status = row.get("skyslope_status")

    if saleguid is None:
        return "no_skyslope_record"

    is_cancelled = (
        be_status
        and be_status.lower() == "cancelled"
        and skyslope_status
        and skyslope_status.lower() in ["canceled/pend", "canceled/app"]
    )
    if is_cancelled:
        return None

    be_contract_date = row.get("be_contract_date")
    skyslope_contract_date = row.get("skyslope_contract_date")

    if be_contract_date is None or skyslope_contract_date is None:
        return None

    return "match" if be_contract_date == skyslope_contract_date else "mismatch"



def build_reconciliation_data_row(row: dict) -> tuple:
    eval_result = evaluate_row(row)
    contract_date_match = get_contract_date_match(row)
    evaluated_at = datetime.utcnow()

    return (
        row.get("transactionid"),
        row.get("be_source_table"),
        row.get("saleguid"),
        row.get("property_address"),
        row.get("be_close_date"),
        row.get("be_status"),
        row.get("be_transaction_specialist"),
        row.get("skyslope_reviewer"),

        eval_result["gross_commission"]["be_value"],
        eval_result["gross_commission"]["skyslope_value"],
        eval_result["gross_commission"]["match_result"],

        row.get("be_close_date_value"),
        row.get("skyslope_close_date_value"),
        eval_result["close_date"]["match_result"],

        row.get("be_status_value"),
        row.get("skyslope_status_value"),
        eval_result["status"]["match_result"],

        eval_result["sale_price"]["be_value"],
        eval_result["sale_price"]["skyslope_value"],
        eval_result["sale_price"]["match_result"],

        eval_result["listing_price"]["be_value"],
        eval_result["listing_price"]["skyslope_value"],
        eval_result["listing_price"]["match_result"],

        row.get("be_contract_date"),
        row.get("skyslope_contract_date"),
        contract_date_match,

        eval_result["buyer_name"]["be_value"],
        eval_result["buyer_name"]["skyslope_value"],
        eval_result["buyer_name"]["match_result"],

        eval_result["seller_name"]["be_value"],
        eval_result["seller_name"]["skyslope_value"],
        eval_result["seller_name"]["match_result"],

        eval_result["buying_agent_name"]["be_value"],
        eval_result["buying_agent_name"]["skyslope_value"],
        eval_result["buying_agent_name"]["match_result"],

        eval_result["title_company"]["be_value"],
        eval_result["title_company"]["skyslope_value"],
        eval_result["title_company"]["match_result"],

        evaluated_at,
    )


@router.post("/reconciliation/data/populate")
def populate_reconciliation_data(conn=Depends(get_db)):
    select_query = f"""
        {RECONCILIATION_DATA_BASE_QUERY}
        ORDER BY transactionid
    """

    insert_sql = f"""
        INSERT INTO reconciliation_data (
            {", ".join(INSERT_COLUMNS)}
        )
        VALUES %s
        ON CONFLICT (transactionid) DO UPDATE SET
            be_source_table = EXCLUDED.be_source_table,
            saleguid = EXCLUDED.saleguid,
            property_address = EXCLUDED.property_address,
            be_close_date = EXCLUDED.be_close_date,
            be_status = EXCLUDED.be_status,
            be_transaction_specialist = EXCLUDED.be_transaction_specialist,
            skyslope_reviewer = EXCLUDED.skyslope_reviewer,
            be_gross_commission = EXCLUDED.be_gross_commission,
            skyslope_gross_commission = EXCLUDED.skyslope_gross_commission,
            gross_commission_match = EXCLUDED.gross_commission_match,
            be_close_date_value = EXCLUDED.be_close_date_value,
            skyslope_close_date_value = EXCLUDED.skyslope_close_date_value,
            close_date_match = EXCLUDED.close_date_match,
            be_status_value = EXCLUDED.be_status_value,
            skyslope_status_value = EXCLUDED.skyslope_status_value,
            status_match = EXCLUDED.status_match,
            be_sale_price = EXCLUDED.be_sale_price,
            skyslope_sale_price = EXCLUDED.skyslope_sale_price,
            sale_price_match = EXCLUDED.sale_price_match,
            be_listing_price = EXCLUDED.be_listing_price,
            skyslope_listing_price = EXCLUDED.skyslope_listing_price,
            listing_price_match = EXCLUDED.listing_price_match,
            be_contract_date = EXCLUDED.be_contract_date,
            skyslope_contract_date = EXCLUDED.skyslope_contract_date,
            contract_date_match = EXCLUDED.contract_date_match,
            be_buyer_name = EXCLUDED.be_buyer_name,
            skyslope_buyer_name = EXCLUDED.skyslope_buyer_name,
            buyer_name_match = EXCLUDED.buyer_name_match,
            be_seller_name = EXCLUDED.be_seller_name,
            skyslope_seller_name = EXCLUDED.skyslope_seller_name,
            seller_name_match = EXCLUDED.seller_name_match,
            be_buying_agent_name = EXCLUDED.be_buying_agent_name,
            skyslope_buying_agent_name = EXCLUDED.skyslope_buying_agent_name,
            buying_agent_match = EXCLUDED.buying_agent_match,
            be_title_company = EXCLUDED.be_title_company,
            skyslope_title_company = EXCLUDED.skyslope_title_company,
            title_company_match = EXCLUDED.title_company_match,
            evaluated_at = EXCLUDED.evaluated_at
    """

    processed_count = 0

    with conn.cursor(name="reconciliation_data_populate_cursor", cursor_factory=RealDictCursor) as read_cur:
        read_cur.itersize = 1000
        read_cur.execute(select_query)

        while True:
            rows = read_cur.fetchmany(1000)
            if not rows:
                break

            batch = [build_reconciliation_data_row(row) for row in rows]

            for i, item in enumerate(batch):
                if not isinstance(item, tuple):
                    raise ValueError(f"Batch row {i} is not tuple. Got: {type(item)}")
                if len(item) != EXPECTED_COLS:
                    raise ValueError(
                        f"Batch row {i} has {len(item)} values, expected {EXPECTED_COLS}. "
                        f"transactionid={item[0] if item else None}"
                    )

            with conn.cursor() as write_cur:
                execute_values(
                    write_cur,
                    insert_sql,
                    batch,
                    page_size=1000,
                )

            processed_count += len(batch)

    conn.commit()

    return {
        "message": "reconciliation_data populated successfully",
        "processed_count": processed_count,
    }