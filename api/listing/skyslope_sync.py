import json
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from db import get_conn
from services.session import get_session_token
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import psycopg2
from psycopg2.extras import execute_values
from fastapi import BackgroundTasks, APIRouter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()


SALES_FILTER_URL = "https://api.skyslope.com/api/files?modifiedAfter=2024-05-01T00:00:00&type=sale"

REQUEST_TIMEOUT = 1000
MAX_RETRIES = 3
BACKOFF_FACTOR = 2
DEFAULT_NUM_WORKERS = 10
BATCH_SIZE = 100


def build_session():
    session = requests.Session()
    retry = Retry(
        total=MAX_RETRIES,
        backoff_factor=BACKOFF_FACTOR,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=100, pool_maxsize=100)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "Content-Type": "application/json",
    })
    return session

HTTP_SESSION = build_session()

progress_lock = Lock()
processed_count = 0
error_count_global = 0
saved_count_global = 0

def create_tables(conn):
    cur = conn.cursor()
    tables = [
        """
        CREATE TABLE IF NOT EXISTS users (
            userGuid uuid NOT NULL PRIMARY KEY
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS office (
            officeGuid uuid NOT NULL PRIMARY KEY,
            officeName varchar
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS checklist (
            typeId int NOT NULL PRIMARY KEY,
            typeName varchar
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sale (
            saleGuid uuid NOT NULL PRIMARY KEY,
            listingGuid uuid,
            agentGuid uuid REFERENCES users(userGuid),
            createdByGuid uuid REFERENCES users(userGuid),
            mlsNumber varchar,
            portalEmail varchar,
            statusId int,
            status varchar,
            officeGuid uuid REFERENCES office(officeGuid),
            checklistTypeId int REFERENCES checklist(typeId),
            escrowNumber varchar,
            escrowClosingDate date,
            actualClosingDate date,
            contractAcceptanceDate date,
            createdOn date,
            checklistModifiedOn date,
            deadDate date,
            reviewerGuid uuid REFERENCES users(userGuid),
            sourceId int,
            source varchar,
            otherSource varchar,
            dealType varchar,
            saleTypeId int,
            listingPrice decimal(15,4),
            salePrice decimal(15,4),
            isOfficeLead boolean,
            coBrokerCompany varchar,
            realPropertyType varchar,
            realPropertySubtype varchar,
            commercialLease varchar,
            stageId int,
            customFields varchar
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sale_property (
            saleGuid uuid NOT NULL PRIMARY KEY REFERENCES sale(saleGuid) ON DELETE CASCADE,
            streetNumber int,
            streetAddress varchar,
            unit varchar,
            direction varchar,
            city varchar,
            county varchar,
            state varchar,
            zip varchar,
            yearBuilt int,
            realPropertyTypeId int,
            realPropertySubtypeId int
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sale_commission (
            saleGuid uuid NOT NULL PRIMARY KEY REFERENCES sale(saleGuid) ON DELETE CASCADE,
            transactionCoordinatorName varchar,
            transactionCoordinatorFee varchar,
            adminBrokerageComp decimal,
            dateOfCheck date,
            datePostedToLogBook date,
            listingCommissionPercent decimal,
            listingCommissionAmount decimal,
            saleCommissionPercent decimal,
            saleCommissionAmount decimal,
            otherDeductions decimal,
            personalDeal boolean,
            commissionBreakdownDetails varchar,
            officeGrossCommissionOnSale decimal
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sale_contact (
            saleGuid uuid NOT NULL REFERENCES sale(saleGuid) ON DELETE CASCADE,
            contactGuid uuid NOT NULL,
            role varchar NOT NULL,
            firstName varchar,
            lastName varchar,
            phoneNumber varchar,
            email varchar,
            company varchar,
            alternatePhone varchar,
            streetNumber varchar,
            streetName varchar,
            zip varchar,
            city varchar,
            state varchar,
            fax varchar,
            notes varchar,
            isTrustCompanyOrOtherEntity boolean,
            isCashDeal boolean,
            loanTypeId int,
            loanType varchar,
            loanAmount decimal,
            brokerTaxId int,
            miscContactType varchar,
            PRIMARY KEY (saleGuid, contactGuid, role)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sale_commission_breakdown (
            id serial PRIMARY KEY,
            saleGuid uuid NOT NULL REFERENCES sale(saleGuid) ON DELETE CASCADE,
            name varchar,
            details varchar,
            amount decimal
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sale_co_agent (
            saleGuid uuid NOT NULL REFERENCES sale(saleGuid) ON DELETE CASCADE,
            coAgentGuid uuid NOT NULL REFERENCES users(userGuid),
            PRIMARY KEY (saleGuid, coAgentGuid)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sale_transaction_coordinator (
            saleGuid uuid NOT NULL REFERENCES sale(saleGuid) ON DELETE CASCADE,
            contactGuid uuid,
            firstName varchar,
            lastName varchar,
            fullName varchar,
            email varchar,
            phoneNumber varchar,
            notes varchar,
            fee decimal,
            hasAccess boolean,
            PRIMARY KEY (saleGuid, contactGuid)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sale_commission_split (
            saleGuid uuid NOT NULL REFERENCES sale(saleGuid) ON DELETE CASCADE,
            agentGuid uuid REFERENCES users(userGuid),
            amount decimal,
            percentage decimal,
            PRIMARY KEY (saleGuid, agentGuid)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sale_commission_referral (
            saleGuid uuid NOT NULL PRIMARY KEY REFERENCES sale(saleGuid) ON DELETE CASCADE,
            typeId int,
            typeName varchar,
            contactGuid uuid,
            contactFirstName varchar,
            contactLastName varchar,
            contactEmail varchar,
            contactPhoneNumber varchar,
            brokerageName varchar,
            amount decimal
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sale_earnest_money_deposit (
            saleGuid uuid NOT NULL PRIMARY KEY REFERENCES sale(saleGuid) ON DELETE CASCADE,
            isEarnestMoneyHeld boolean,
            depositAmount decimal,
            depositDueDate date,
            datePostedToLogBook date,
            dateOfCheck date,
            additionalDepositAmount decimal,
            additionalDepositDueDate date
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sale_checklist_activity (
            saleGuid uuid NOT NULL REFERENCES sale(saleGuid) ON DELETE CASCADE,
            activityId varchar,
            "order" int,
            activityName varchar,
            dateAssigned date,
            typeId int,
            typeName varchar,
            status varchar,
            help varchar,
            modifiedOn date,
            PRIMARY KEY (saleGuid, activityId)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sale_checklist_doc (
            saleGuid uuid NOT NULL,
            activityId varchar,
            docId varchar,
            name varchar,
            url varchar,
            documentServiceKey varchar,
            modifiedDate date,
            uploadDate date,
            fileName varchar,
            extension varchar,
            fileSize decimal,
            pages int,
            PRIMARY KEY (docId, saleGuid),
            FOREIGN KEY (activityId, saleGuid)
                REFERENCES sale_checklist_activity(activityId, saleGuid) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sale_checklist_activity_docs (
            saleGuid uuid NOT NULL,
            activityId varchar,
            fileName varchar,
            PRIMARY KEY (saleGuid, activityId, fileName),
            FOREIGN KEY (activityId, saleGuid)
                REFERENCES sale_checklist_activity(activityId, saleGuid) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sale_checklist_comment (
            id serial PRIMARY KEY,
            activityId varchar,
            saleGuid uuid NOT NULL,
            comment varchar,
            createdOn date,
            createdBy varchar,
            FOREIGN KEY (activityId, saleGuid)
                REFERENCES sale_checklist_activity(activityId, saleGuid) ON DELETE CASCADE
        )
        """,
    ]
    for sql in tables:
        cur.execute(sql)
    conn.commit()
    cur.close()

def normalize_date(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        clean = value.split("T")[0].split(" ")[0]
        date_formats = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"]
        for fmt in date_formats:
            try:
                dt = datetime.strptime(clean, fmt)
                if dt.year < 1900:
                    return None
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None
    elif isinstance(value, (int, float)):
        try:
            dt = datetime.fromtimestamp(value)
            return dt.strftime("%Y-%m-%d")
        except (ValueError, OSError):
            return None
    return None

def to_json_text(value):
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple, set)):
        try:
            if isinstance(value, set):
                value = list(value)
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            return str(value)
    return value

def clean_text(value):
    if value is None:
        return None
    value = to_json_text(value)
    if isinstance(value, str):
        value = value.strip()
        return value if value else None
    return str(value).strip() if str(value).strip() else None

def clean_int(value):
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple, set)):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None

def clean_decimal(value):
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple, set)):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

def clean_bool(value):
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple, set)):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower().strip() in ("true", "1", "yes")
    if isinstance(value, (int, float)):
        return bool(value)
    return None

def clean_guid(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().rstrip(":")
    return str(value).strip().rstrip(":")

def fetch_api(url):
    try:
        response = HTTP_SESSION.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        text = response.content.decode("utf-8-sig")
        return json.loads(text)
    except requests.exceptions.Timeout:
        logger.error(f"Timeout fetching: {url}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error: {e}")
        return None
    except ValueError as e:
        logger.error(f"JSON decode error: {e}")
        return None

def fetch_sales():
    sales = []
    url = SALES_FILTER_URL

    try:
        token = get_session_token()
        HTTP_SESSION.headers.update({"Session": token})
    except Exception as e:
        logger.error(f"Failed to obtain session token: {e}")
        return sales

    while url:
        logger.info(f"Fetching page: {url}")
        data = fetch_api(url)
        if not data:
            break

        items = data.get("value", [])
        sale_items = [item for item in items if item.get("saleGuid")]

        sales.extend(sale_items)
        logger.info(f"Retrieved {len(sale_items)} sales (total so far: {len(sales)})")

        url = data.get("@odata.nextLink") or data.get("nextLink")

    logger.info(f"Found {len(sales)} sales in total.")
    return sales

def collect_contacts(sale_item):
    contact_roles = [
        ("seller", "sellers"),
        ("buyer", "buyers"),
        ("lender", "lenderContact"),
        ("titleCompany", "titleContact"),
        ("escrowCompany", "escrowContact"),
        ("listingAgent", "listingAgents"),
        ("attorney", "attorneyContact"),
        ("otherSideAgent", "otherSideAgentContact"),
        ("homeWarranty", "homeWarrantyContact"),
        ("miscContact", "miscContact"),
    ]

    all_contacts = []
    for role, key in contact_roles:
        entries = sale_item.get(key, [])
        if not entries:
            if entries is None or entries == [] or entries == {}:
                continue
        if isinstance(entries, dict):
            if entries:
                entries = [entries]
            else:
                continue
        if isinstance(entries, list):
            for contact in entries:
                if not contact:
                    continue
                contact = dict(contact)
                contact["role"] = clean_text(contact.get("role")) or role
                all_contacts.append(contact)

    return all_contacts

def process_sale(sale_item):
    sale_guid = sale_item.get("saleGuid")
    if not sale_guid:
        return None

    sale_data = dict(sale_item)

    checklist_nested = sale_data.get("checklist", {}) or {}
    checklist_type_id = checklist_nested.get("typeId") or checklist_nested.get("typeID")
    checklist_type_name = checklist_nested.get("typeName")

    if checklist_type_id is not None:
        sale_data["checklistTypeId"] = checklist_type_id
    if checklist_type_name is not None and sale_data.get("checklistType") is None:
        sale_data["checklistType"] = checklist_type_name

    stage_nested = sale_data.get("stage", {}) or {}
    if isinstance(stage_nested, dict) and stage_nested.get("id") is not None:
        sale_data["stageId"] = stage_nested.get("id")

    activities_data = checklist_nested.get("activities", [])

    property_data = sale_data.get("property", {}) or {}
    commission_data = sale_data.get("commission", {}) or {}
    contacts_data = collect_contacts(sale_data)
    breakdown_data = sale_data.get("commissionBreakdowns", []) or []

    co_agents_raw = sale_data.get("coAgentGuids", []) or []
    if not co_agents_raw:
        co_agents_alt = sale_data.get("coAgents", []) or []
        if co_agents_alt:
            co_agents_raw = [a.get("guid") for a in co_agents_alt if a.get("guid")]

    co_agents_data = [{"coAgentGuid": g} for g in co_agents_raw] if co_agents_raw else []

    coordinators_data = sale_data.get("transactionCoordinators", []) or []
    splits_data = sale_data.get("commissionSplits", []) or []
    referral_data = sale_data.get("commissionReferral", {}) or {}
    emd_data = sale_data.get("earnestMoneyDeposit", {}) or {}

    all_docs, all_activity_docs, all_comments = [], [], []

    for activity in activities_data:
        activity_id = activity.get("activityId")

        for doc in activity.get("docs", []):
            if isinstance(doc, str):
                all_activity_docs.append({"activityId": activity_id, "fileName": doc})
            else:
                doc = dict(doc)
                doc["activityId"] = activity_id
                doc["docId"] = doc.get("id") or doc.get("docId") or doc.get("documentGuid")
                all_docs.append(doc)

        for doc in activity.get("documents", []):
            doc = dict(doc)
            doc["activityId"] = activity_id
            doc["docId"] = doc.get("documentGuid") or doc.get("id")
            doc["fileName"] = doc.get("fileName") or doc.get("name")
            all_docs.append(doc)

        for ad in activity.get("checklistDocs", []):
            ad = dict(ad)
            ad["activityId"] = activity_id
            ad["docId"] = ad.get("id") or ad.get("docId")
            all_docs.append(ad)

        for ad in activity.get("activityDocs", []):
            if isinstance(ad, str):
                all_activity_docs.append({"activityId": activity_id, "fileName": ad})
            else:
                ad = dict(ad)
                ad["activityId"] = activity_id
                all_activity_docs.append(ad)

        for comment in activity.get("comments", []):
            comment = dict(comment)
            comment["activityId"] = activity_id
            all_comments.append(comment)

    return {
        "sale": sale_data,
        "property": property_data,
        "commission": commission_data,
        "contacts": contacts_data,
        "breakdown": breakdown_data,
        "co_agents": co_agents_data,
        "coordinators": coordinators_data,
        "splits": splits_data,
        "referral": referral_data,
        "emd": emd_data,
        "activities": activities_data,
        "docs": all_docs,
        "activity_docs": all_activity_docs,
        "comments": all_comments,
    }

def deduplicate_rows(rows, key_indices):
    seen = set()
    unique = []
    for r in reversed(rows):
        k = tuple(r[i] for i in key_indices)
        if k not in seen:
            seen.add(k)
            unique.append(r)
    return unique[::-1]

def process_sale_batch(sales_batch, worker_id):
    global processed_count, saved_count_global, error_count_global

    conn = get_conn()
    try:
        cur = conn.cursor()
        
        users_to_ensure = set()
        offices_to_ensure = {}
        checklists_to_ensure = {}
        
        sale_guids_in_batch = set()
        
        # Table data accumulators
        sales_rows = []
        property_rows = []
        commission_rows = []
        contact_rows = []
        breakdown_rows = []
        co_agent_rows = []
        coordinator_rows = []
        split_rows = []
        referral_rows = []
        emd_rows = []
        activity_rows = []
        doc_rows = []
        activity_doc_rows = []
        comment_rows = []

        batch_saved = 0
        
        for sale_item in sales_batch:
            sale_guid = clean_guid(sale_item.get("saleGuid"))
            if not sale_guid:
                continue
                
            data = process_sale(sale_item)
            if not data:
                continue

            sale_data = data["sale"]
            sale_guids_in_batch.add(sale_guid)
            
            # Users
            for field in ("createdByGuid", "agentGuid", "reviewerGuid"):
                u = clean_text(sale_data.get(field))
                if u: users_to_ensure.add(u)
            for row in data.get("co_agents", []):
                u = clean_text(row.get("coAgentGuid") or row.get("userGuid"))
                if u: users_to_ensure.add(u)
            for row in data.get("splits", []):
                u = clean_text(row.get("agentGuid") or row.get("userGuid"))
                if u: users_to_ensure.add(u)
                
            # Checklist & Office
            chk_id = clean_int(sale_data.get("checklistTypeId"))
            chk_name = clean_text(sale_data.get("checklistType"))
            if chk_id is not None:
                checklists_to_ensure[chk_id] = chk_name
                
            off_guid = clean_text(sale_data.get("officeGuid"))
            off_name = clean_text(sale_data.get("officeName"))
            if off_guid:
                offices_to_ensure[off_guid] = off_name
                
            # Sale
            custom_fields = to_json_text(sale_data.get("customFields"))
            if isinstance(custom_fields, str):
                custom_fields = custom_fields.strip() or None

            sales_rows.append((
                sale_guid, clean_text(sale_data.get("listingGuid")), clean_text(sale_data.get("agentGuid")),
                clean_text(sale_data.get("createdByGuid")), clean_text(sale_data.get("mlsNumber")),
                clean_text(sale_data.get("portalEmail") or sale_data.get("email")), clean_int(sale_data.get("statusId")),
                clean_text(sale_data.get("status")), clean_text(sale_data.get("officeGuid")),
                clean_int(sale_data.get("checklistTypeId")), clean_text(sale_data.get("escrowNumber")),
                normalize_date(sale_data.get("escrowClosingDate")), normalize_date(sale_data.get("actualClosingDate")),
                normalize_date(sale_data.get("contractAcceptanceDate")), normalize_date(sale_data.get("createdOn")),
                normalize_date(sale_data.get("checklistModifiedOn")), normalize_date(sale_data.get("deadDate")),
                clean_text(sale_data.get("reviewerGuid")), clean_int(sale_data.get("sourceId")),
                clean_text(sale_data.get("source")), clean_text(sale_data.get("otherSource")),
                clean_text(sale_data.get("dealType")), clean_int(sale_data.get("saleTypeId")),
                clean_decimal(sale_data.get("listingPrice")), clean_decimal(sale_data.get("salePrice")),
                clean_bool(sale_data.get("isOfficeLead")), clean_text(sale_data.get("coBrokerCompany")),
                clean_text(sale_data.get("realPropertyType")), clean_text(sale_data.get("realPropertySubtype")),
                clean_text(sale_data.get("commercialLease")), clean_int(sale_data.get("stageId")), custom_fields
            ))
            
            # Property
            pd = data.get("property", {})
            if pd:
                property_rows.append((
                    sale_guid, clean_int(pd.get("streetNumber")), clean_text(pd.get("streetAddress")),
                    clean_text(pd.get("unit")), clean_text(pd.get("direction")), clean_text(pd.get("city")),
                    clean_text(pd.get("county")), clean_text(pd.get("state")), clean_text(pd.get("zip")),
                    clean_int(pd.get("yearBuilt")), clean_int(pd.get("realPropertyTypeId")),
                    clean_int(pd.get("realPropertySubtypeId"))
                ))

            # Commission
            cd = data.get("commission", {})
            if cd:
                commission_rows.append((
                    sale_guid, clean_text(cd.get("transactionCoordinatorName")), clean_text(cd.get("transactionCoordinatorFee")),
                    clean_decimal(cd.get("adminBrokerageComp")), normalize_date(cd.get("dateOfCheck")),
                    normalize_date(cd.get("datePostedToLogBook")), clean_decimal(cd.get("listingCommissionPercent")),
                    clean_decimal(cd.get("listingCommissionAmount")), clean_decimal(cd.get("saleCommissionPercent")),
                    clean_decimal(cd.get("saleCommissionAmount")), clean_decimal(cd.get("otherDeductions")),
                    clean_bool(cd.get("personalDeal")), clean_text(cd.get("commissionBreakdownDetails")),
                    clean_decimal(cd.get("officeGrossCommissionOnSale"))
                ))

            # Contacts
            for contact in data.get("contacts", []):
                c_guid = clean_text(contact.get("contactGuid"))
                role = clean_text(contact.get("role"))
                if c_guid and role:
                    contact_rows.append((
                        sale_guid, c_guid, role, clean_text(contact.get("firstName")), clean_text(contact.get("lastName")),
                        clean_text(contact.get("phoneNumber")), clean_text(contact.get("email")), clean_text(contact.get("company")),
                        clean_text(contact.get("alternatePhone")), clean_text(contact.get("streetNumber")), clean_text(contact.get("streetName")),
                        clean_text(contact.get("zip")), clean_text(contact.get("city")), clean_text(contact.get("state")),
                        clean_text(contact.get("fax")), clean_text(contact.get("notes")), clean_bool(contact.get("isTrustCompanyOrOtherEntity")),
                        clean_bool(contact.get("isCashDeal")), clean_int(contact.get("loanTypeId")), clean_text(contact.get("loanType")),
                        clean_decimal(contact.get("loanAmount")), clean_int(contact.get("brokerTaxId")), clean_text(contact.get("miscContactType"))
                    ))
                    
            # Breakdown
            for i in data.get("breakdown", []):
                name = clean_text(i.get("name"))
                if name:
                    breakdown_rows.append((sale_guid, name, clean_text(i.get("details")), clean_decimal(i.get("amount"))))

            # Co Agents
            for i in data.get("co_agents", []):
                cg = clean_text(i.get("coAgentGuid") or i.get("userGuid"))
                if cg:
                    co_agent_rows.append((sale_guid, cg))

            # Transaction Coordinators
            for i in data.get("coordinators", []):
                cg = clean_text(i.get("contactGuid"))
                if cg:
                    coordinator_rows.append((
                        sale_guid, cg, clean_text(i.get("firstName")), clean_text(i.get("lastName")),
                        clean_text(i.get("fullName")), clean_text(i.get("email")), clean_text(i.get("phoneNumber")),
                        clean_text(i.get("notes")), clean_decimal(i.get("fee")), clean_bool(i.get("hasAccess"))
                    ))

            # Commission Splits
            for i in data.get("splits", []):
                ag = clean_text(i.get("agentGuid") or i.get("userGuid"))
                if ag:
                    split_rows.append((sale_guid, ag, clean_decimal(i.get("amount")), clean_decimal(i.get("percentage"))))

            # Referral
            rd = data.get("referral", {})
            if rd:
                t_obj = rd.get("type", {}) or {}
                referral_rows.append((
                    sale_guid, clean_int(t_obj.get("id")) or clean_int(rd.get("typeId")),
                    clean_text(t_obj.get("name")) or clean_text(rd.get("typeName")),
                    clean_text(rd.get("contactGuid")) or clean_text(rd.get("agentGuid")),
                    clean_text(rd.get("contactFirstName")), clean_text(rd.get("contactLastName")),
                    clean_text(rd.get("contactEmail")), clean_text(rd.get("contactPhoneNumber")),
                    clean_text(rd.get("brokerageName")), clean_decimal(rd.get("amount"))
                ))

            # EMD
            emd = data.get("emd", {})
            if emd:
                emd_rows.append((
                    sale_guid, clean_bool(emd.get("isEarnestMoneyHeld")), clean_decimal(emd.get("depositAmount")),
                    normalize_date(emd.get("depositDueDate")), normalize_date(emd.get("datePostedToLogBook")),
                    normalize_date(emd.get("dateOfCheck")), clean_decimal(emd.get("additionalDepositAmount")),
                    normalize_date(emd.get("additionalDepositDueDate"))
                ))

            # Activities
            for item in data.get("activities", []):
                aid = clean_text(item.get("activityId"))
                if aid:
                    activity_rows.append((
                        sale_guid, aid, clean_int(item.get("order")), clean_text(item.get("activityName")),
                        normalize_date(item.get("dateAssigned")), clean_int(item.get("typeId")), clean_text(item.get("typeName")),
                        clean_text(item.get("status")), clean_text(item.get("help")), normalize_date(item.get("modifiedOn"))
                    ))

            # Docs
            for item in data.get("docs", []):
                did = clean_text(item.get("docId"))
                if did:
                    doc_rows.append((
                        sale_guid, clean_text(item.get("activityId")), did, clean_text(item.get("name")), clean_text(item.get("url")),
                        clean_text(item.get("documentServiceKey")), normalize_date(item.get("modifiedDate")),
                        normalize_date(item.get("uploadDate")), clean_text(item.get("fileName")), clean_text(item.get("extension")),
                        clean_decimal(item.get("fileSize")), clean_int(item.get("pages"))
                    ))

            # Activity Docs
            for i in data.get("activity_docs", []):
                aid = clean_text(i.get("activityId"))
                fn = clean_text(i.get("fileName"))
                if aid and fn:
                    activity_doc_rows.append((sale_guid, aid, fn))

            # Comments
            for i in data.get("comments", []):
                aid = clean_text(i.get("activityId"))
                if aid:
                    comment_rows.append((
                        aid, sale_guid, clean_text(i.get("comment")), normalize_date(i.get("createdOn")), clean_text(i.get("createdBy"))
                    ))
            
            batch_saved += 1
            
        if not sales_rows:
            return

        # Insert meta tables directly into main tables (users, checklist, office)
        if users_to_ensure:
            try:
                cur.execute("SAVEPOINT ensure_user_sp")
                execute_values(cur, "INSERT INTO users (userGuid) VALUES %s ON CONFLICT (userGuid) DO NOTHING", [(u,) for u in users_to_ensure])
                cur.execute("RELEASE SAVEPOINT ensure_user_sp")
            except psycopg2.Error:
                cur.execute("ROLLBACK TO SAVEPOINT ensure_user_sp")

        if checklists_to_ensure:
            execute_values(cur, "INSERT INTO checklist (typeId, typeName) VALUES %s ON CONFLICT (typeId) DO NOTHING", list(checklists_to_ensure.items()))

        if offices_to_ensure:
            execute_values(cur, "INSERT INTO office (officeGuid, officeName) VALUES %s ON CONFLICT (officeGuid) DO NOTHING", list(offices_to_ensure.items()))

        # Direct upsert into main tables (no staging/copy tables)

        if sales_rows:
            execute_values(cur, """
            INSERT INTO sale (
                saleGuid, listingGuid, agentGuid, createdByGuid,
                mlsNumber, portalEmail, statusId, status,
                officeGuid, checklistTypeId, escrowNumber,
                escrowClosingDate, actualClosingDate, contractAcceptanceDate,
                createdOn, checklistModifiedOn, deadDate,
                reviewerGuid, sourceId, source, otherSource,
                dealType, saleTypeId, listingPrice, salePrice,
                isOfficeLead, coBrokerCompany, realPropertyType, realPropertySubtype,
                commercialLease, stageId, customFields
            ) VALUES %s
            ON CONFLICT (saleGuid) DO UPDATE SET
                listingGuid = EXCLUDED.listingGuid,
                agentGuid = EXCLUDED.agentGuid,
                createdByGuid = EXCLUDED.createdByGuid,
                mlsNumber = EXCLUDED.mlsNumber,
                portalEmail = EXCLUDED.portalEmail,
                statusId = EXCLUDED.statusId,
                status = EXCLUDED.status,
                officeGuid = EXCLUDED.officeGuid,
                checklistTypeId = EXCLUDED.checklistTypeId,
                escrowNumber = EXCLUDED.escrowNumber,
                escrowClosingDate = EXCLUDED.escrowClosingDate,
                actualClosingDate = EXCLUDED.actualClosingDate,
                contractAcceptanceDate = EXCLUDED.contractAcceptanceDate,
                createdOn = EXCLUDED.createdOn,
                checklistModifiedOn = EXCLUDED.checklistModifiedOn,
                deadDate = EXCLUDED.deadDate,
                reviewerGuid = EXCLUDED.reviewerGuid,
                sourceId = EXCLUDED.sourceId,
                source = EXCLUDED.source,
                otherSource = EXCLUDED.otherSource,
                dealType = EXCLUDED.dealType,
                saleTypeId = EXCLUDED.saleTypeId,
                listingPrice = EXCLUDED.listingPrice,
                salePrice = EXCLUDED.salePrice,
                isOfficeLead = EXCLUDED.isOfficeLead,
                coBrokerCompany = EXCLUDED.coBrokerCompany,
                realPropertyType = EXCLUDED.realPropertyType,
                realPropertySubtype = EXCLUDED.realPropertySubtype,
                commercialLease = EXCLUDED.commercialLease,
                stageId = EXCLUDED.stageId,
                customFields = EXCLUDED.customFields
            """, deduplicate_rows(sales_rows, [0]))

        if property_rows:
            execute_values(cur, """
            INSERT INTO sale_property (
                saleGuid, streetNumber, streetAddress, unit, direction,
                city, county, state, zip, yearBuilt,
                realPropertyTypeId, realPropertySubtypeId
            ) VALUES %s
            ON CONFLICT (saleGuid) DO UPDATE SET
                streetNumber = EXCLUDED.streetNumber,
                streetAddress = EXCLUDED.streetAddress,
                unit = EXCLUDED.unit,
                direction = EXCLUDED.direction,
                city = EXCLUDED.city,
                county = EXCLUDED.county,
                state = EXCLUDED.state,
                zip = EXCLUDED.zip,
                yearBuilt = EXCLUDED.yearBuilt,
                realPropertyTypeId = EXCLUDED.realPropertyTypeId,
                realPropertySubtypeId = EXCLUDED.realPropertySubtypeId
            """, deduplicate_rows(property_rows, [0]))

        if commission_rows:
            execute_values(cur, """
            INSERT INTO sale_commission (
                saleGuid, transactionCoordinatorName, transactionCoordinatorFee,
                adminBrokerageComp, dateOfCheck, datePostedToLogBook,
                listingCommissionPercent, listingCommissionAmount,
                saleCommissionPercent, saleCommissionAmount,
                otherDeductions, personalDeal, commissionBreakdownDetails,
                officeGrossCommissionOnSale
            ) VALUES %s
            ON CONFLICT (saleGuid) DO UPDATE SET
                transactionCoordinatorName = EXCLUDED.transactionCoordinatorName,
                transactionCoordinatorFee = EXCLUDED.transactionCoordinatorFee,
                adminBrokerageComp = EXCLUDED.adminBrokerageComp,
                dateOfCheck = EXCLUDED.dateOfCheck,
                datePostedToLogBook = EXCLUDED.datePostedToLogBook,
                listingCommissionPercent = EXCLUDED.listingCommissionPercent,
                listingCommissionAmount = EXCLUDED.listingCommissionAmount,
                saleCommissionPercent = EXCLUDED.saleCommissionPercent,
                saleCommissionAmount = EXCLUDED.saleCommissionAmount,
                otherDeductions = EXCLUDED.otherDeductions,
                personalDeal = EXCLUDED.personalDeal,
                commissionBreakdownDetails = EXCLUDED.commissionBreakdownDetails,
                officeGrossCommissionOnSale = EXCLUDED.officeGrossCommissionOnSale
            """, deduplicate_rows(commission_rows, [0]))

        if contact_rows:
            execute_values(cur, """
            INSERT INTO sale_contact (
                saleGuid, contactGuid, role, firstName, lastName,
                phoneNumber, email, company, alternatePhone,
                streetNumber, streetName, zip, city, state,
                fax, notes, isTrustCompanyOrOtherEntity, isCashDeal,
                loanTypeId, loanType, loanAmount, brokerTaxId, miscContactType
            ) VALUES %s
            ON CONFLICT (saleGuid, contactGuid, role) DO UPDATE SET
                firstName = EXCLUDED.firstName,
                lastName = EXCLUDED.lastName,
                phoneNumber = EXCLUDED.phoneNumber,
                email = EXCLUDED.email,
                company = EXCLUDED.company,
                alternatePhone = EXCLUDED.alternatePhone,
                streetNumber = EXCLUDED.streetNumber,
                streetName = EXCLUDED.streetName,
                zip = EXCLUDED.zip,
                city = EXCLUDED.city,
                state = EXCLUDED.state,
                fax = EXCLUDED.fax,
                notes = EXCLUDED.notes,
                isTrustCompanyOrOtherEntity = EXCLUDED.isTrustCompanyOrOtherEntity,
                isCashDeal = EXCLUDED.isCashDeal,
                loanTypeId = EXCLUDED.loanTypeId,
                loanType = EXCLUDED.loanType,
                loanAmount = EXCLUDED.loanAmount,
                brokerTaxId = EXCLUDED.brokerTaxId,
                miscContactType = EXCLUDED.miscContactType
            """, deduplicate_rows(contact_rows, [0, 1, 2]))

        if co_agent_rows:
            execute_values(cur, """
            INSERT INTO sale_co_agent (saleGuid, coAgentGuid) VALUES %s
            ON CONFLICT (saleGuid, coAgentGuid) DO NOTHING
            """, deduplicate_rows(co_agent_rows, [0, 1]))

        if coordinator_rows:
            execute_values(cur, """
            INSERT INTO sale_transaction_coordinator (
                saleGuid, contactGuid, firstName, lastName, fullName,
                email, phoneNumber, notes, fee, hasAccess
            ) VALUES %s
            ON CONFLICT (saleGuid, contactGuid) DO UPDATE SET
                firstName = EXCLUDED.firstName,
                lastName = EXCLUDED.lastName,
                fullName = EXCLUDED.fullName,
                email = EXCLUDED.email,
                phoneNumber = EXCLUDED.phoneNumber,
                notes = EXCLUDED.notes,
                fee = EXCLUDED.fee,
                hasAccess = EXCLUDED.hasAccess
            """, deduplicate_rows(coordinator_rows, [0, 1]))

        if split_rows:
            execute_values(cur, """
            INSERT INTO sale_commission_split (saleGuid, agentGuid, amount, percentage)
            VALUES %s
            ON CONFLICT (saleGuid, agentGuid) DO UPDATE SET
                amount = EXCLUDED.amount,
                percentage = EXCLUDED.percentage
            """, deduplicate_rows(split_rows, [0, 1]))

        if referral_rows:
            execute_values(cur, """
            INSERT INTO sale_commission_referral (
                saleGuid, typeId, typeName, contactGuid,
                contactFirstName, contactLastName, contactEmail, contactPhoneNumber,
                brokerageName, amount
            ) VALUES %s
            ON CONFLICT (saleGuid) DO UPDATE SET
                typeId = EXCLUDED.typeId,
                typeName = EXCLUDED.typeName,
                contactGuid = EXCLUDED.contactGuid,
                contactFirstName = EXCLUDED.contactFirstName,
                contactLastName = EXCLUDED.contactLastName,
                contactEmail = EXCLUDED.contactEmail,
                contactPhoneNumber = EXCLUDED.contactPhoneNumber,
                brokerageName = EXCLUDED.brokerageName,
                amount = EXCLUDED.amount
            """, deduplicate_rows(referral_rows, [0]))

        if emd_rows:
            execute_values(cur, """
            INSERT INTO sale_earnest_money_deposit (
                saleGuid, isEarnestMoneyHeld, depositAmount, depositDueDate,
                datePostedToLogBook, dateOfCheck, additionalDepositAmount, additionalDepositDueDate
            ) VALUES %s
            ON CONFLICT (saleGuid) DO UPDATE SET
                isEarnestMoneyHeld = EXCLUDED.isEarnestMoneyHeld,
                depositAmount = EXCLUDED.depositAmount,
                depositDueDate = EXCLUDED.depositDueDate,
                datePostedToLogBook = EXCLUDED.datePostedToLogBook,
                dateOfCheck = EXCLUDED.dateOfCheck,
                additionalDepositAmount = EXCLUDED.additionalDepositAmount,
                additionalDepositDueDate = EXCLUDED.additionalDepositDueDate
            """, deduplicate_rows(emd_rows, [0]))

        if activity_rows:
            execute_values(cur, """
            INSERT INTO sale_checklist_activity (
                saleGuid, activityId, "order", activityName, dateAssigned,
                typeId, typeName, status, help, modifiedOn
            ) VALUES %s
            ON CONFLICT (saleGuid, activityId) DO UPDATE SET
                "order" = EXCLUDED."order",
                activityName = EXCLUDED.activityName,
                dateAssigned = EXCLUDED.dateAssigned,
                typeId = EXCLUDED.typeId,
                typeName = EXCLUDED.typeName,
                status = EXCLUDED.status,
                help = EXCLUDED.help,
                modifiedOn = EXCLUDED.modifiedOn
            """, deduplicate_rows(activity_rows, [0, 1]))

        if doc_rows:
            execute_values(cur, """
            INSERT INTO sale_checklist_doc (
                saleGuid, activityId, docId, name, url,
                documentServiceKey, modifiedDate, uploadDate, fileName,
                extension, fileSize, pages
            ) VALUES %s
            ON CONFLICT (docId, saleGuid) DO UPDATE SET
                activityId = EXCLUDED.activityId,
                name = EXCLUDED.name,
                url = EXCLUDED.url,
                documentServiceKey = EXCLUDED.documentServiceKey,
                modifiedDate = EXCLUDED.modifiedDate,
                uploadDate = EXCLUDED.uploadDate,
                fileName = EXCLUDED.fileName,
                extension = EXCLUDED.extension,
                fileSize = EXCLUDED.fileSize,
                pages = EXCLUDED.pages
            """, deduplicate_rows(doc_rows, [2, 0]))

        if activity_doc_rows:
            execute_values(cur, """
            INSERT INTO sale_checklist_activity_docs (saleGuid, activityId, fileName)
            VALUES %s
            ON CONFLICT (saleGuid, activityId, fileName) DO NOTHING
            """, deduplicate_rows(activity_doc_rows, [0, 1, 2]))

        # For tables with serial PKs, delete existing rows for this batch then re-insert
        if sale_guids_in_batch:
            guid_list = list(sale_guids_in_batch)
            cur.execute("DELETE FROM sale_commission_breakdown WHERE saleGuid = ANY(%s::uuid[])", (guid_list,))
            cur.execute("DELETE FROM sale_checklist_comment WHERE saleGuid = ANY(%s::uuid[])", (guid_list,))

        if breakdown_rows:
            execute_values(cur, """
            INSERT INTO sale_commission_breakdown (saleGuid, name, details, amount)
            VALUES %s""", breakdown_rows)

        if comment_rows:
            execute_values(cur, """
            INSERT INTO sale_checklist_comment (activityId, saleGuid, comment, createdOn, createdBy)
            VALUES %s""", comment_rows)

        conn.commit()
        
        with progress_lock:
            saved_count_global += batch_saved
            processed_count += len(sales_batch)
            if processed_count % 100 == 0:
                logger.info(f"Progress: {processed_count} sales processed...")

    except Exception as e:
        conn.rollback()
        with progress_lock:
            error_count_global += len(sales_batch)
        logger.error(f"  [WORKER-{worker_id} BATCH ERROR] {e}")
        
    finally:
        if cur:
            cur.close()
        conn.close()


@router.post("/sync/skyslope-sales")
def trigger_sales_sync(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_sync_job)
    return {"message": "SkySlope Sales sync started in the background."}

def run_sync_job():
    global processed_count, saved_count_global, error_count_global
    processed_count = 0
    saved_count_global = 0
    error_count_global = 0

    logger.info("Starting background sync job...")
    sales = fetch_sales()
    if not sales:
        logger.info("No sales found to sync.")
        return

    total_sales = len(sales)
    logger.info(f"Found {total_sales} sales to process.")

    # Process all sales in parallel batches (direct upsert, no copy tables)
    batches = [sales[i:i + BATCH_SIZE] for i in range(0, total_sales, BATCH_SIZE)]

    with ThreadPoolExecutor(max_workers=DEFAULT_NUM_WORKERS) as executor:
        futures = []
        for idx, batch in enumerate(batches):
            futures.append(executor.submit(process_sale_batch, batch, idx))

        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logger.error(f"Batch processing error: {e}")

    logger.info(f"Sync completed! Saved: {saved_count_global}, Errors: {error_count_global}")

