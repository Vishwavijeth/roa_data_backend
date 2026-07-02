from fastapi import APIRouter, Depends, Query
from psycopg2.extras import RealDictCursor
from db import get_db

router = APIRouter()

@router.get("/checklist-type-validation")
def checklist_type_validation(
    page: int = Query(default=1, ge=1),
    type_of_sale: list[str] | None = Query(default=None),
    search: str | None = Query(default=None),
    conn=Depends(get_db)
):
    try:
        limit = 50
        offset = (page - 1) * limit
        params = []

        validation_case = """
            CASE
                WHEN s.dealtype = 'Listing' THEN
                    CASE
                        WHEN LOWER(COALESCE(c.typename, '')) LIKE '%%listing%%'
                             OR LOWER(COALESCE(c.typename, '')) LIKE '%%seller%%'
                        THEN 'match'
                        ELSE 'mismatch'
                    END

                WHEN s.dealtype = 'Both Purchase & Listing' THEN
                    CASE
                        WHEN LOWER(COALESCE(c.typename, '')) LIKE '%%dual%%'
                             OR LOWER(COALESCE(c.typename, '')) LIKE '%%intermediary%%'
                        THEN 'match'
                        ELSE 'mismatch'
                    END

                WHEN s.dealtype = 'Lease Tenant' THEN
                    CASE
                        WHEN LOWER(COALESCE(c.typename, '')) LIKE '%%lease%%'
                             OR LOWER(COALESCE(c.typename, '')) LIKE '%%tenant%%'
                             OR LOWER(COALESCE(c.typename, '')) LIKE '%%rental%%'
                             OR LOWER(COALESCE(c.typename, '')) LIKE '%%apartment%%'
                        THEN 'match'
                        ELSE 'mismatch'
                    END

                WHEN s.dealtype = 'Lease Landlord' THEN
                    CASE
                        WHEN LOWER(COALESCE(c.typename, '')) LIKE '%%landlord%%'
                        THEN 'match'
                        ELSE 'mismatch'
                    END

                WHEN s.dealtype = 'Referral' THEN
                    CASE
                        WHEN LOWER(COALESCE(c.typename, '')) LIKE '%%referral%%'
                        THEN 'match'
                        ELSE 'mismatch'
                    END

                WHEN s.dealtype = 'BPO' THEN
                    CASE
                        WHEN LOWER(COALESCE(c.typename, '')) LIKE '%%bpo%%'
                        THEN 'match'
                        ELSE 'mismatch'
                    END

                WHEN s.dealtype = 'Both Lease Tenant & Landlord' THEN
                    CASE
                        WHEN LOWER(COALESCE(c.typename, '')) LIKE '%%tx | lease intermediary%%'
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
            WHERE ({validation_case}) = 'mismatch'
        """

        if type_of_sale:
            cleaned_type_of_sale = [x.strip() for x in type_of_sale if x and x.strip()]
            if cleaned_type_of_sale:
                base_query += " AND s.dealtype = ANY(%s)"
                params.append(cleaned_type_of_sale)

        if search and search.strip():
            base_query += f" AND {property_address_expr} ILIKE %s"
            params.append(f"%{search.strip()}%")

        count_query = f"""
            SELECT COUNT(*) AS total_count
            {base_query}
        """

        data_query = f"""
            SELECT
                s.saleguid,
                s.url AS url,
                {property_address_expr} AS propertyaddress,
                s.dealtype AS type_of_sale,
                c.typename AS checklist_type_name,
                ({validation_case}) AS match_result
            {base_query}
            ORDER BY s.saleguid
            LIMIT %s OFFSET %s
        """

        data_params = params + [limit, offset]

        type_of_sale_query = """
            SELECT DISTINCT dealtype
            FROM sale
            WHERE dealtype IS NOT NULL AND TRIM(dealtype) <> ''
            ORDER BY dealtype
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(count_query, params)
            total_count = cur.fetchone()["total_count"]

            cur.execute(data_query, data_params)
            rows = cur.fetchall()

            cur.execute(type_of_sale_query)
            type_of_sale_list = [row["dealtype"] for row in cur.fetchall()]

        return {
            "total_count": total_count,
            "page": page,
            "page_size": limit,
            "filters": {
                "type_of_sale": type_of_sale_list
            },
            "data": rows
        }

    finally:
        pass