from psycopg2.extras import RealDictCursor
from db import get_conn


def load_data():
    conn = get_conn()
    try:
        return get_sales(conn), get_be(conn)
    finally:
        conn.close()


def get_sales(conn):
    query = """ 
                SELECT
                    s.saleguid,
                    s.listingguid,
                    s.saleprice,
                    s.escrowclosingdate,
                    s.listingprice,
                    s.mlsnumber,
                    s.contractacceptancedate,
                    s.status,

                    -- property address
                    CONCAT_WS(', ',
                        CONCAT_WS(' ', sp.streetnumber, sp.streetaddress),
                        sp.city,
                        sp.state,
                        sp.zip
                    ) AS propertyaddress,

                    COALESCE(u.firstname || ' ' || u.lastname, '') as agent_full_name,
                    COALESCE(u.email, '') AS agent_mail_id,
                    COALESCE(r.firstname || ' ' || r.lastname, '') as reviewer_full_name,

                    -- buyer names
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
                    ) AS buyer_full_name,

                    -- seller names
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
                    ) AS seller_full_name,

                    -- agent name from users table
                    COALESCE(
                        (
                            SELECT TRIM(COALESCE(uu.firstname, '') || ' ' || COALESCE(uu.lastname, ''))
                            FROM users uu
                            WHERE uu.userguid = s.agentguid
                        ),
                        ''
                    ) AS skyslope_buying_agent_name,

                    COALESCE(scn.officeGrossCommissionOnSale, 0) AS officegrosscommissiononsale,

                    -- title company (FIXED)
                    COALESCE(
                        (
                            SELECT sc.company
                            FROM sale_contact sc
                            WHERE sc.saleguid = s.saleguid
                            AND LOWER(sc.role) = 'titlecompany'
                            LIMIT 1
                        ),
                        ''
                    ) AS title_company

                FROM sale s

                LEFT JOIN users u
                    ON s.agentguid = u.userguid

                LEFT JOIN users r
                    ON s.reviewerguid = r.userguid

                LEFT JOIN sale_property sp
                    ON s.saleguid = sp.saleguid

                LEFT JOIN sale_commission scn
                    ON scn.saleguid = s.saleguid;
    
     """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query)
        return cur.fetchall()


def get_be(conn):
    query = """ 
            SELECT
            skyslopefileid,
            listingguid,
            listing_office,
            transaction_identifier_transactionid,
            sale_price,
            closed_date,
            listing_price,
            contract_date,
            tags,
            buyer_name,
            seller_name,
            buying_agent_name,
            total_gross_commission,
            transaction_specialist,
            property_address,
            skyslopefileid,
            da_title_company
        FROM brokerage_engine
     """

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query)
        return cur.fetchall()
    
