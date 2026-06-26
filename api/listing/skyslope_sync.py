import json
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import psycopg2
from psycopg2.extras import execute_values
from fastapi import APIRouter
from db import get_conn
from services.session import get_session_token

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger(__name__)

router = APIRouter()

SALES_BASE_URL = "https://api.skyslope.com/api/files"
SALES_FILTER_TYPE = "sale"
DEFAULT_SYNC_DATE = "2024-01-01"

REQUEST_TIMEOUT = 300
MAX_RETRIES = 3
BACKOFF_FACTOR = 2
DEFAULT_NUM_WORKERS = 10
BATCH_SIZE = 100

DEBUG_SAMPLE_LIMIT = 10

progress_lock = Lock()
processed_count = 0
error_count_global = 0
saved_count_global = 0


def get_last_sync_date() -> str:
    try:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS skyslope_sync (
                id serial PRIMARY KEY,
                sync_date date,
                sync_timestamp timestamp,
                status varchar,
                error_message varchar
            )
        """)

        cur.execute("""
            SELECT sync_date
            FROM skyslope_sync
            ORDER BY id DESC
            LIMIT 1
        """)

        row = cur.fetchone()
        cur.close()
        conn.close()

        if row and row[0]:
            date_str = row[0].strftime("%Y-%m-%d")
            logger.info(f"Last sync date loaded from DB: {date_str}")
            return date_str

    except Exception as e:
        logger.warning(f"Could not read sync date from DB, using default: {e}")

    logger.info(f"No sync date found in DB. Using default: {DEFAULT_SYNC_DATE}")
    return DEFAULT_SYNC_DATE


def update_sync_date():
    now = datetime.now()

    try:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO skyslope_sync (
                sync_date,
                sync_timestamp,
                status
            )
            VALUES (%s, NOW(), %s)
        """, (now.date(), "success"))

        conn.commit()
        cur.close()
        conn.close()

        logger.info(f"Sync date inserted into DB: {now.date()}")

    except Exception as e:
        logger.error(f"Failed to insert sync date into DB: {e}")


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
        value = value.strip().rstrip(":")
        return value or None
    value = str(value).strip().rstrip(":")
    return value or None


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

    sync_date = get_last_sync_date()
    modified_after = f"{sync_date}T00:00:00"
    url = f"{SALES_BASE_URL}?modifiedAfter={modified_after}&type={SALES_FILTER_TYPE}"
    logger.info(f"Fetching sales modified after: {modified_after}")

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
    file_creator_data = sale_data.get("fileCreator", {}) or {}
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
        "file_creator": file_creator_data,
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


def log_dedup_stats(worker_id, table_name, original_rows, deduped_rows):
    dropped = len(original_rows) - len(deduped_rows)
    if dropped > 0:
        logger.info(
            f"[WORKER-{worker_id}] {table_name}: before={len(original_rows)}, "
            f"after={len(deduped_rows)}, dedup_dropped={dropped}"
        )


def process_sale_batch(sales_batch, worker_id):
    global processed_count, saved_count_global, error_count_global

    conn = get_conn()
    cur = None

    skipped_no_guid = 0
    skipped_process_sale = 0
    sample_no_guid = []
    sample_process_sale_none = []

    try:
        cur = conn.cursor()

        users_to_ensure = set()
        offices_to_ensure = {}
        checklists_to_ensure = {}

        sale_guids_in_batch = set()

        sales_rows = []
        property_rows = []
        commission_rows = []
        file_creator_rows = []
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

        for idx, sale_item in enumerate(sales_batch):
            raw_sale_guid = sale_item.get("saleGuid")
            sale_guid = clean_guid(raw_sale_guid)

            if not sale_guid:
                skipped_no_guid += 1
                if len(sample_no_guid) < DEBUG_SAMPLE_LIMIT:
                    sample_no_guid.append(raw_sale_guid)
                continue

            data = process_sale(sale_item)
            if not data:
                skipped_process_sale += 1
                if len(sample_process_sale_none) < DEBUG_SAMPLE_LIMIT:
                    sample_process_sale_none.append(sale_guid)
                continue

            sale_data = data["sale"]
            sale_guids_in_batch.add(sale_guid)

            for field in ("createdByGuid", "agentGuid", "reviewerGuid"):
                u = clean_text(sale_data.get(field))
                if u:
                    users_to_ensure.add(u)

            for row in data.get("co_agents", []):
                u = clean_text(row.get("coAgentGuid") or row.get("userGuid"))
                if u:
                    users_to_ensure.add(u)

            for row in data.get("splits", []):
                u = clean_text(row.get("agentGuid") or row.get("userGuid"))
                if u:
                    users_to_ensure.add(u)

            fc = data.get("file_creator", {}) or {}
            fc_guid = clean_text(fc.get("guid"))
            if fc_guid:
                users_to_ensure.add(fc_guid)

            chk_id = clean_int(sale_data.get("checklistTypeId"))
            chk_name = clean_text(sale_data.get("checklistType"))
            if chk_id is not None:
                checklists_to_ensure[chk_id] = chk_name

            off_guid = clean_text(sale_data.get("officeGuid"))
            off_name = clean_text(sale_data.get("officeName"))
            if off_guid:
                offices_to_ensure[off_guid] = off_name

            custom_fields = to_json_text(sale_data.get("customFields"))
            if isinstance(custom_fields, str):
                custom_fields = custom_fields.strip() or None

            sales_rows.append((
                clean_text(sale_data.get("objectType")),
                sale_guid,
                clean_text(sale_data.get("listingGuid")),
                clean_text(sale_data.get("agentGuid")),
                clean_text(sale_data.get("createdByGuid")),
                clean_text(sale_data.get("mlsNumber")),
                clean_text(sale_data.get("portalEmail") or sale_data.get("email")),
                clean_int(sale_data.get("statusId")),
                clean_text(sale_data.get("status")),
                clean_text(sale_data.get("officeGuid")),
                clean_int(sale_data.get("checklistTypeId")),
                clean_text(sale_data.get("escrowNumber")),
                normalize_date(sale_data.get("escrowClosingDate")),
                normalize_date(sale_data.get("actualClosingDate")),
                normalize_date(sale_data.get("contractAcceptanceDate")),
                normalize_date(sale_data.get("createdOn")),
                normalize_date(sale_data.get("checklistModifiedOn")),
                normalize_date(sale_data.get("deadDate")),
                clean_text(sale_data.get("reviewerGuid")),
                clean_int(sale_data.get("sourceId")),
                clean_text(sale_data.get("source")),
                clean_text(sale_data.get("otherSource")),
                clean_text(sale_data.get("dealType")),
                clean_int(sale_data.get("saleTypeId")),
                clean_decimal(sale_data.get("listingPrice")),
                clean_decimal(sale_data.get("salePrice")),
                clean_bool(sale_data.get("isOfficeLead")),
                clean_text(sale_data.get("coBrokerCompany")),
                clean_text(sale_data.get("realPropertyType")),
                clean_text(sale_data.get("realPropertySubtype")),
                clean_text(sale_data.get("commercialLease")),
                clean_int(sale_data.get("stageId")),
                custom_fields,
                clean_text(sale_data.get("fileId")),
                clean_text(sale_data.get("url"))
            ))

            pd = data.get("property", {})
            if pd:
                property_rows.append((
                    sale_guid, clean_int(pd.get("streetNumber")), clean_text(pd.get("streetAddress")),
                    clean_text(pd.get("unit")), clean_text(pd.get("direction")), clean_text(pd.get("city")),
                    clean_text(pd.get("county")), clean_text(pd.get("state")), clean_text(pd.get("zip")),
                    clean_int(pd.get("yearBuilt")), clean_int(pd.get("realPropertyTypeId")),
                    clean_int(pd.get("realPropertySubtypeId"))
                ))

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

            if fc_guid:
                file_creator_rows.append((
                    sale_guid,
                    fc_guid,
                    clean_text(fc.get("firstName")),
                    clean_text(fc.get("lastName")),
                    clean_text(fc.get("email")),
                    clean_text(fc.get("alternateEmail"))
                ))

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

            for i in data.get("breakdown", []):
                name = clean_text(i.get("name"))
                if name:
                    breakdown_rows.append((sale_guid, name, clean_text(i.get("details")), clean_decimal(i.get("amount"))))

            for i in data.get("co_agents", []):
                cg = clean_text(i.get("coAgentGuid") or i.get("userGuid"))
                if cg:
                    co_agent_rows.append((sale_guid, cg))

            for i in data.get("coordinators", []):
                cg = clean_text(i.get("contactGuid"))
                if cg:
                    coordinator_rows.append((
                        sale_guid, cg, clean_text(i.get("firstName")), clean_text(i.get("lastName")),
                        clean_text(i.get("fullName")), clean_text(i.get("email")), clean_text(i.get("phoneNumber")),
                        clean_text(i.get("notes")), clean_decimal(i.get("fee")), clean_bool(i.get("hasAccess"))
                    ))

            for i in data.get("splits", []):
                ag = clean_text(i.get("agentGuid") or i.get("userGuid"))
                if ag:
                    split_rows.append((sale_guid, ag, clean_decimal(i.get("amount")), clean_decimal(i.get("percentage"))))

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

            emd = data.get("emd", {})
            if emd:
                emd_rows.append((
                    sale_guid, clean_bool(emd.get("isEarnestMoneyHeld")), clean_decimal(emd.get("depositAmount")),
                    normalize_date(emd.get("depositDueDate")), normalize_date(emd.get("datePostedToLogBook")),
                    normalize_date(emd.get("dateOfCheck")), clean_decimal(emd.get("additionalDepositAmount")),
                    normalize_date(emd.get("additionalDepositDueDate"))
                ))

            for item in data.get("activities", []):
                aid = clean_text(item.get("activityId"))
                if aid:
                    activity_rows.append((
                        sale_guid, aid, clean_int(item.get("order")), clean_text(item.get("activityName")),
                        normalize_date(item.get("dateAssigned")), clean_int(item.get("typeId")), clean_text(item.get("typeName")),
                        clean_text(item.get("status")), clean_text(item.get("help")), normalize_date(item.get("modifiedOn"))
                    ))

            for item in data.get("docs", []):
                did = clean_text(item.get("docId"))
                if did:
                    doc_rows.append((
                        sale_guid, clean_text(item.get("activityId")), did, clean_text(item.get("name")), clean_text(item.get("url")),
                        clean_text(item.get("documentServiceKey")), normalize_date(item.get("modifiedDate")),
                        normalize_date(item.get("uploadDate")), clean_text(item.get("fileName")), clean_text(item.get("extension")),
                        clean_decimal(item.get("fileSize")), clean_int(item.get("pages"))
                    ))

            for i in data.get("activity_docs", []):
                aid = clean_text(i.get("activityId"))
                fn = clean_text(i.get("fileName"))
                if aid and fn:
                    activity_doc_rows.append((sale_guid, aid, fn))

            for i in data.get("comments", []):
                aid = clean_text(i.get("activityId"))
                if aid:
                    comment_rows.append((
                        aid, sale_guid, clean_text(i.get("comment")), normalize_date(i.get("createdOn")), clean_text(i.get("createdBy"))
                    ))

            batch_saved += 1

        if sample_no_guid:
            logger.info(f"[WORKER-{worker_id}] sample skipped_no_guid: {sample_no_guid}")

        if sample_process_sale_none:
            logger.info(f"[WORKER-{worker_id}] sample skipped_process_sale: {sample_process_sale_none}")

        if not sales_rows:
            logger.warning(
                f"[WORKER-{worker_id}] No sales_rows built. "
                f"batch_size={len(sales_batch)}, skipped_no_guid={skipped_no_guid}, skipped_process_sale={skipped_process_sale}"
            )
            return

        sales_rows_dedup = deduplicate_rows(sales_rows, [1])
        property_rows_dedup = deduplicate_rows(property_rows, [0]) if property_rows else []
        commission_rows_dedup = deduplicate_rows(commission_rows, [0]) if commission_rows else []
        file_creator_rows_dedup = deduplicate_rows(file_creator_rows, [0, 1]) if file_creator_rows else []
        contact_rows_dedup = deduplicate_rows(contact_rows, [0, 1, 2]) if contact_rows else []
        co_agent_rows_dedup = deduplicate_rows(co_agent_rows, [0, 1]) if co_agent_rows else []
        coordinator_rows_dedup = deduplicate_rows(coordinator_rows, [0, 1]) if coordinator_rows else []
        split_rows_dedup = deduplicate_rows(split_rows, [0, 1]) if split_rows else []
        referral_rows_dedup = deduplicate_rows(referral_rows, [0]) if referral_rows else []
        emd_rows_dedup = deduplicate_rows(emd_rows, [0]) if emd_rows else []
        activity_rows_dedup = deduplicate_rows(activity_rows, [0, 1]) if activity_rows else []
        doc_rows_dedup = deduplicate_rows(doc_rows, [2, 0]) if doc_rows else []
        activity_doc_rows_dedup = deduplicate_rows(activity_doc_rows, [0, 1, 2]) if activity_doc_rows else []

        log_dedup_stats(worker_id, "sale", sales_rows, sales_rows_dedup)
        log_dedup_stats(worker_id, "sale_property", property_rows, property_rows_dedup)
        log_dedup_stats(worker_id, "sale_commission", commission_rows, commission_rows_dedup)
        log_dedup_stats(worker_id, "sale_file_creator", file_creator_rows, file_creator_rows_dedup)
        log_dedup_stats(worker_id, "sale_contact", contact_rows, contact_rows_dedup)
        log_dedup_stats(worker_id, "sale_co_agent", co_agent_rows, co_agent_rows_dedup)
        log_dedup_stats(worker_id, "sale_transaction_coordinator", coordinator_rows, coordinator_rows_dedup)
        log_dedup_stats(worker_id, "sale_commission_split", split_rows, split_rows_dedup)
        log_dedup_stats(worker_id, "sale_commission_referral", referral_rows, referral_rows_dedup)
        log_dedup_stats(worker_id, "sale_earnest_money_deposit", emd_rows, emd_rows_dedup)
        log_dedup_stats(worker_id, "sale_checklist_activity", activity_rows, activity_rows_dedup)
        log_dedup_stats(worker_id, "sale_checklist_doc", doc_rows, doc_rows_dedup)
        log_dedup_stats(worker_id, "sale_checklist_activity_docs", activity_doc_rows, activity_doc_rows_dedup)

        if users_to_ensure:
            try:
                cur.execute("SAVEPOINT ensure_user_sp")
                execute_values(
                    cur,
                    "INSERT INTO users (userGuid) VALUES %s ON CONFLICT (userGuid) DO NOTHING",
                    [(u,) for u in users_to_ensure]
                )
                cur.execute("RELEASE SAVEPOINT ensure_user_sp")
            except psycopg2.Error as e:
                cur.execute("ROLLBACK TO SAVEPOINT ensure_user_sp")
                logger.warning(f"[WORKER-{worker_id}] users ensure skipped due to error: {e}")

        if checklists_to_ensure:
            execute_values(
                cur,
                "INSERT INTO checklist (typeId, typeName) VALUES %s ON CONFLICT (typeId) DO NOTHING",
                list(checklists_to_ensure.items())
            )

        if offices_to_ensure:
            execute_values(
                cur,
                "INSERT INTO office (officeGuid, officeName) VALUES %s ON CONFLICT (officeGuid) DO NOTHING",
                list(offices_to_ensure.items())
            )

        execute_values(cur, """
        INSERT INTO sale (
            transaction_type,
            saleGuid,
            listingGuid,
            agentGuid,
            createdByGuid,
            mlsNumber,
            Email,
            statusId,
            status,
            officeGuid,
            checklistTypeId,
            escrowNumber,
            escrowClosingDate,
            actualClosingDate,
            contractAcceptanceDate,
            createdOn,
            checklistModifiedOn,
            deadDate,
            reviewerGuid,
            sourceId,
            source,
            otherSource,
            dealType,
            saleTypeId,
            listingPrice,
            salePrice,
            isOfficeLead,
            coBrokerCompany,
            realPropertyType,
            realPropertySubtype,
            commercialLease,
            stageId,
            customFields,
            fileid,
            url
        ) VALUES %s
        ON CONFLICT (saleGuid) DO UPDATE SET
            transaction_type = EXCLUDED.transaction_type,
            listingGuid = EXCLUDED.listingGuid,
            agentGuid = EXCLUDED.agentGuid,
            createdByGuid = EXCLUDED.createdByGuid,
            mlsNumber = EXCLUDED.mlsNumber,
            Email = EXCLUDED.Email,
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
            customFields = EXCLUDED.customFields,
            fileid = EXCLUDED.fileid,
            url = EXCLUDED.url
        """, sales_rows_dedup)

        if file_creator_rows_dedup:
            execute_values(cur, """
            INSERT INTO sale_file_creator (
                saleguid,
                guid,
                firstname,
                lastname,
                email,
                alternateemail
            ) VALUES %s
            ON CONFLICT (saleguid, guid) DO UPDATE SET
                firstname = EXCLUDED.firstname,
                lastname = EXCLUDED.lastname,
                email = EXCLUDED.email,
                alternateemail = EXCLUDED.alternateemail
            """, file_creator_rows_dedup)

        if property_rows_dedup:
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
            """, property_rows_dedup)

        if commission_rows_dedup:
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
            """, commission_rows_dedup)

        if contact_rows_dedup:
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
            """, contact_rows_dedup)

        if co_agent_rows_dedup:
            execute_values(cur, """
            INSERT INTO sale_co_agent (saleGuid, coAgentGuid) VALUES %s
            ON CONFLICT (saleGuid, coAgentGuid) DO NOTHING
            """, co_agent_rows_dedup)

        if coordinator_rows_dedup:
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
            """, coordinator_rows_dedup)

        if split_rows_dedup:
            execute_values(cur, """
            INSERT INTO sale_commission_split (saleGuid, agentGuid, amount, percentage)
            VALUES %s
            ON CONFLICT (saleGuid, agentGuid) DO UPDATE SET
                amount = EXCLUDED.amount,
                percentage = EXCLUDED.percentage
            """, split_rows_dedup)

        if referral_rows_dedup:
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
            """, referral_rows_dedup)

        if emd_rows_dedup:
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
            """, emd_rows_dedup)

        if activity_rows_dedup:
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
            """, activity_rows_dedup)

        if doc_rows_dedup:
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
            """, doc_rows_dedup)

        if activity_doc_rows_dedup:
            execute_values(cur, """
            INSERT INTO sale_checklist_activity_docs (saleGuid, activityId, fileName)
            VALUES %s
            ON CONFLICT (saleGuid, activityId, fileName) DO NOTHING
            """, activity_doc_rows_dedup)

        if sale_guids_in_batch:
            guid_list = list(sale_guids_in_batch)
            cur.execute("DELETE FROM sale_commission_breakdown WHERE saleGuid = ANY(%s::uuid[])", (guid_list,))
            cur.execute("DELETE FROM sale_checklist_comment WHERE saleGuid = ANY(%s::uuid[])", (guid_list,))

        if breakdown_rows:
            execute_values(cur, """
            INSERT INTO sale_commission_breakdown (saleGuid, name, details, amount)
            VALUES %s
            """, breakdown_rows)

        if comment_rows:
            execute_values(cur, """
            INSERT INTO sale_checklist_comment (activityId, saleGuid, comment, createdOn, createdBy)
            VALUES %s
            """, comment_rows)

        conn.commit()

        logger.info(
            f"[WORKER-{worker_id}] batch summary: "
            f"input={len(sales_batch)}, valid_sales={batch_saved}, skipped_no_guid={skipped_no_guid}, "
            f"skipped_process_sale={skipped_process_sale}, sale_rows={len(sales_rows)}, "
            f"sale_rows_after_dedup={len(sales_rows_dedup)}"
        )

        with progress_lock:
            saved_count_global += batch_saved
            processed_count += len(sales_batch)
            if processed_count % 100 == 0:
                logger.info(f"Progress: {processed_count} sales processed...")

    except Exception as e:
        conn.rollback()
        with progress_lock:
            error_count_global += len(sales_batch)
        logger.error(f"[WORKER-{worker_id} BATCH ERROR] {e}", exc_info=True)

    finally:
        if cur:
            cur.close()
        conn.close()


@router.post("/sync/skyslope-sales")
def trigger_sales_sync():
    global processed_count, saved_count_global, error_count_global
    processed_count = 0
    saved_count_global = 0
    error_count_global = 0

    logger.info("Starting sync job...")
    sales = fetch_sales()
    if not sales:
        logger.info("No sales found to sync.")
        return {"message": "No sales found to sync.", "saved": 0, "errors": 0}

    total_sales = len(sales)
    logger.info(f"Found {total_sales} sales to process.")

    batches = [sales[i:i + BATCH_SIZE] for i in range(0, total_sales, BATCH_SIZE)]

    with ThreadPoolExecutor(max_workers=DEFAULT_NUM_WORKERS) as executor:
        futures = []
        for idx, batch in enumerate(batches):
            futures.append(executor.submit(process_sale_batch, batch, idx))

        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logger.error(f"Batch processing error: {e}", exc_info=True)

    logger.info(
        f"Sync completed! total_fetched={total_sales}, "
        f"saved={saved_count_global}, errors={error_count_global}"
    )

    update_sync_date()

    return {
        "message": "Sync completed successfully.",
        "total_fetched": total_sales,
        "saved": saved_count_global,
        "errors": error_count_global,
    }