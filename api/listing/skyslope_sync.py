import json
import logging
import time
import psycopg2
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Any, Dict, List, Optional, Sequence, Tuple
from fastapi import APIRouter, Depends
from psycopg2.extras import execute_values
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from db import get_db, get_conn
from services.skyslope_sync.utils import (
    normalize_date, clean_bool, clean_decimal, 
    clean_guid, clean_int, clean_text, 
    clean_url, to_json_text, get_last_sync_date, update_sync_date
    )
from services.skyslope_sync.insert_queries import (
    SALE_UPSERT_SQL, SPLIT_UPSERT_SQL, DOC_UPSERT_SQL, EMD_UPSERT_SQL,
    COMMENT_INSERT_SQL, CONTACT_UPSERT_SQL, ACTIVITY_UPSERT_SQL,
    CO_AGENT_UPSERT_SQL, PROPERTY_UPSERT_SQL, REFERRAL_UPSERT_SQL,
    BREAKDOWN_INSERT_SQL, COMMISSION_UPSERT_SQL, COORDINATOR_UPSERT_SQL, 
    ACTIVITY_DOC_UPSERT_SQL, FILE_CREATOR_UPSERT_SQL
)
from services.session import get_session_token

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger(__name__)

router = APIRouter()

SALES_BASE_URL = "https://api.skyslope.com/api/files"
SALE_DETAIL_URL_TEMPLATE = "https://api.skyslope.com/api/files/sales/{saleGuid}"
SALES_FILTER_TYPE = "sale"
DEFAULT_SYNC_DATE = "2024-01-01"

REQUEST_TIMEOUT = 300
HTTP_RETRY_TOTAL = 3
HTTP_BACKOFF_FACTOR = 1

API_DETAIL_WORKERS = 2
BATCH_SIZE = 50
DEBUG_SAMPLE_LIMIT = 10
FAILED_SALEGUID_RETRY_ROUNDS = 1

REQUEST_GAP_SECONDS = 0.5
RATE_LIMIT_FALLBACK_SLEEP_SECONDS = 10

progress_lock = Lock()
failed_saleguids_lock = Lock()
detail_cache_lock = Lock()
request_gap_lock = Lock()

processed_count = 0
error_count_global = 0
saved_count_global = 0
failed_saleguids_global = set()
detail_cache: Dict[str, Dict[str, Any]] = {}
last_request_ts = 0.0


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=HTTP_RETRY_TOTAL,
        backoff_factor=HTTP_BACKOFF_FACTOR,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=20,
        pool_maxsize=20,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"Content-Type": "application/json"})
    return session


HTTP_SESSION = build_session()


def wait_for_request_gap() -> None:
    global last_request_ts

    with request_gap_lock:
        now = time.time()
        elapsed = now - last_request_ts
        if elapsed < REQUEST_GAP_SECONDS:
            time.sleep(REQUEST_GAP_SECONDS - elapsed)
        last_request_ts = time.time()


def get_rate_limit_sleep_seconds(response: requests.Response) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(float(retry_after), 1.0)
        except ValueError:
            pass

    reset_ts = clean_int(response.headers.get("x-ratelimit-reset"))
    if reset_ts:
        wait_for = reset_ts - int(time.time())
        if wait_for > 0:
            return float(wait_for)

    return float(RATE_LIMIT_FALLBACK_SLEEP_SECONDS)


def fetch_api(url: str) -> Optional[Dict[str, Any]]:
    for attempt in range(1, HTTP_RETRY_TOTAL + 1):
        try:
            wait_for_request_gap()
            response = HTTP_SESSION.get(url, timeout=REQUEST_TIMEOUT)

            if response.status_code == 429:
                sleep_for = get_rate_limit_sleep_seconds(response)
                logger.warning(
                    "SkySlope 429 for url=%s attempt=%s sleeping %.2fs",
                    url, attempt, sleep_for
                )
                time.sleep(sleep_for)
                continue

            response.raise_for_status()
            text = response.content.decode("utf-8-sig")
            return json.loads(text)

        except requests.exceptions.Timeout:
            logger.error("Timeout fetching: %s", url)
            break
        except requests.exceptions.RequestException as e:
            logger.error("Request error for %s: %s", url, e)
            break
        except ValueError as e:
            logger.error("JSON decode error for %s: %s", url, e)
            break

    return None


def fetch_sales_bulk(sync_date: str) -> List[Dict[str, Any]]:
    sales: List[Dict[str, Any]] = []

    modified_after = sync_date
    url = f"{SALES_BASE_URL}?modifiedAfter={modified_after}&type={SALES_FILTER_TYPE}"

    logger.info("Fetching bulk sales modified after: %s", modified_after)

    try:
        token = get_session_token()
        HTTP_SESSION.headers.update({"Session": token})
    except Exception as e:
        logger.error("Failed to obtain session token: %s", e)
        return sales

    while url:
        logger.info("Fetching bulk page: %s", url)
        data = fetch_api(url)
        if not data:
            break

        items = data.get("value", [])
        sale_items = [item for item in items if item.get("saleGuid")]
        sales.extend(sale_items)

        logger.info("Retrieved %s bulk sales (total so far: %s)", len(sale_items), len(sales))
        url = data.get("@odata.nextLink") or data.get("nextLink")

    logger.info("Found %s bulk sales in total.", len(sales))
    return sales


def deduplicate_sales_by_guid(sales: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    latest_by_guid: Dict[str, Dict[str, Any]] = {}

    for item in sales:
        sale_guid = clean_guid(item.get("saleGuid"))
        if not sale_guid:
            continue
        latest_by_guid[sale_guid] = item

    deduped = list(latest_by_guid.values())
    logger.info("Deduplicated bulk sales from %s to %s", len(sales), len(deduped))
    return deduped


def fetch_sale_detail(sale_guid: str) -> Optional[Dict[str, Any]]:
    if not sale_guid:
        return None

    with detail_cache_lock:
        cached = detail_cache.get(sale_guid)
        if cached is not None:
            return cached

    url = SALE_DETAIL_URL_TEMPLATE.format(saleGuid=sale_guid)
    data = fetch_api(url)
    if not data:
        return None

    if isinstance(data.get("value"), dict):
        detail = data["value"]
    elif isinstance(data.get("sale"), dict):
        detail = data
    elif isinstance(data, dict):
        detail = data
    else:
        return None

    with detail_cache_lock:
        detail_cache[sale_guid] = detail

    return detail


def merge_bulk_into_detail(detail_data: Dict[str, Any], bulk_item: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(detail_data)

    for key, value in bulk_item.items():
        if key not in merged or merged.get(key) in (None, "", [], {}):
            merged[key] = value

    merged["saleGuid"] = clean_guid(merged.get("saleGuid") or bulk_item.get("saleGuid"))
    merged["fileId"] = clean_text(bulk_item.get("fileId"))
    merged["url"] = clean_url(bulk_item.get("url"))

    if not merged.get("portalEmail") and bulk_item.get("portalEmail"):
        merged["portalEmail"] = bulk_item.get("portalEmail")

    if not merged.get("email") and bulk_item.get("email"):
        merged["email"] = bulk_item.get("email")

    if not merged.get("objectType") and bulk_item.get("objectType"):
        merged["objectType"] = bulk_item.get("objectType")

    return merged


def collect_contacts(sale_item: Dict[str, Any]) -> List[Dict[str, Any]]:
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

    all_contacts: List[Dict[str, Any]] = []

    for default_role, key in contact_roles:
        entries = sale_item.get(key, [])
        if not entries:
            continue

        if isinstance(entries, dict):
            entries = [entries] if entries else []

        if not isinstance(entries, list):
            continue

        for contact in entries:
            if not contact:
                continue
            row = dict(contact)
            row["role"] = clean_text(row.get("role")) or default_role
            all_contacts.append(row)

    return all_contacts


def derive_sale_url(sale_data: Dict[str, Any], bulk_url: Optional[str], all_docs: List[Dict[str, Any]]) -> Optional[str]:
    if bulk_url:
        return clean_url(bulk_url)

    sale_url = clean_url(sale_data.get("url"))
    if sale_url:
        return sale_url

    for doc in all_docs:
        doc_url = clean_url(doc.get("url"))
        if doc_url:
            return doc_url

    return None


def process_sale(detail_item: Dict[str, Any], bulk_item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    merged_item = merge_bulk_into_detail(detail_item, bulk_item)
    sale_guid = merged_item.get("saleGuid")

    if not sale_guid:
        return None

    sale_data = dict(merged_item)

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

    if not sale_data.get("createdOn") and sale_data.get("modifiedOn"):
        sale_data["createdOn"] = sale_data.get("modifiedOn")

    activities_data = checklist_nested.get("activities", []) or []
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

    all_docs: List[Dict[str, Any]] = []
    all_activity_docs: List[Dict[str, Any]] = []
    all_comments: List[Dict[str, Any]] = []

    for activity in activities_data:
        activity_id = activity.get("activityId")

        for doc in activity.get("docs", []) or []:
            if isinstance(doc, str):
                all_activity_docs.append({"activityId": activity_id, "fileName": doc})
            else:
                row = dict(doc)
                row["activityId"] = activity_id
                row["docId"] = row.get("id") or row.get("docId") or row.get("documentGuid")
                all_docs.append(row)

        for doc in activity.get("documents", []) or []:
            row = dict(doc)
            row["activityId"] = activity_id
            row["docId"] = row.get("documentGuid") or row.get("id")
            row["fileName"] = row.get("fileName") or row.get("name")
            all_docs.append(row)

        for ad in activity.get("checklistDocs", []) or []:
            row = dict(ad)
            row["activityId"] = activity_id
            row["docId"] = row.get("id") or row.get("docId")
            all_docs.append(row)

        for ad in activity.get("activityDocs", []) or []:
            if isinstance(ad, str):
                all_activity_docs.append({"activityId": activity_id, "fileName": ad})
            else:
                row = dict(ad)
                row["activityId"] = activity_id
                all_activity_docs.append(row)

        for comment in activity.get("comments", []) or []:
            row = dict(comment)
            row["activityId"] = activity_id
            all_comments.append(row)

    bulk_file_id = clean_text(bulk_item.get("fileId"))
    bulk_url = clean_url(bulk_item.get("url"))

    sale_data["fileId"] = bulk_file_id
    sale_data["url"] = derive_sale_url(sale_data, bulk_url, all_docs)

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


def deduplicate_rows(rows: Sequence[Tuple], key_indices: Sequence[int]) -> List[Tuple]:
    seen = set()
    unique = []

    for row in reversed(rows):
        key = tuple(row[i] for i in key_indices)
        if key not in seen:
            seen.add(key)
            unique.append(row)

    return unique[::-1]


def log_dedup_stats(worker_id: int, table_name: str, original_rows: Sequence[Tuple], deduped_rows: Sequence[Tuple]) -> None:
    dropped = len(original_rows) - len(deduped_rows)
    if dropped > 0:
        logger.info(
            "[WORKER-%s] %s: before=%s, after=%s, dedup_dropped=%s",
            worker_id, table_name, len(original_rows), len(deduped_rows), dropped
        )


def bulk_execute_values(cur, sql: str, rows: Sequence[Tuple], label: str) -> None:
    if rows:
        execute_values(cur, sql, rows)
        logger.info("Executed bulk upsert for %s with rows=%s", label, len(rows))


def ensure_reference_data(cur, worker_id: int, users: set, checklists: Dict[int, Optional[str]], offices: Dict[str, Optional[str]]) -> None:
    if users:
        try:
            cur.execute("SAVEPOINT ensure_user_sp")
            execute_values(
                cur,
                "INSERT INTO users (userGuid) VALUES %s ON CONFLICT (userGuid) DO NOTHING",
                [(u,) for u in users]
            )
            cur.execute("RELEASE SAVEPOINT ensure_user_sp")
        except psycopg2.Error as e:
            cur.execute("ROLLBACK TO SAVEPOINT ensure_user_sp")
            logger.warning("[WORKER-%s] users ensure skipped due to error: %s", worker_id, e)

    if checklists:
        execute_values(
            cur,
            "INSERT INTO checklist (typeId, typeName) VALUES %s ON CONFLICT (typeId) DO NOTHING",
            list(checklists.items())
        )

    if offices:
        execute_values(
            cur,
            "INSERT INTO office (officeGuid, officeName) VALUES %s ON CONFLICT (officeGuid) DO NOTHING",
            list(offices.items())
        )


def build_sale_row(sale_data: Dict[str, Any]) -> Tuple:
    custom_fields = to_json_text(sale_data.get("customFields"))
    if isinstance(custom_fields, str):
        custom_fields = custom_fields.strip() or None

    sale_guid = clean_guid(sale_data.get("saleGuid"))
    sale_file_id = clean_text(sale_data.get("fileId"))
    sale_url = clean_url(sale_data.get("url"))

    return (
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
        sale_file_id,
        sale_url,
    )

def register_failed_saleguid(sale_guid: str) -> None:
    if not sale_guid:
        return
    with failed_saleguids_lock:
        failed_saleguids_global.add(sale_guid)


def build_retry_sales_from_failed_guids(failed_sale_guids: Sequence[str]) -> List[Dict[str, Any]]:
    return [{"saleGuid": guid} for guid in failed_sale_guids if clean_guid(guid)]


def process_sale_batch(sales_batch: List[Dict[str, Any]], worker_id: int, retry_round: int = 0) -> None:
    global processed_count, saved_count_global, error_count_global

    cur = None

    skipped_no_guid = 0
    skipped_detail_missing = 0
    skipped_process_sale = 0

    sample_no_guid = []
    sample_detail_missing = []
    sample_process_sale_none = []

    try:
        with get_conn() as conn:
            cur = conn.cursor()

            users_to_ensure = set()
            offices_to_ensure: Dict[str, Optional[str]] = {}
            checklists_to_ensure: Dict[int, Optional[str]] = {}
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

            for bulk_item in sales_batch:
                raw_sale_guid = bulk_item.get("saleGuid")
                sale_guid = clean_guid(raw_sale_guid)

                if not sale_guid:
                    skipped_no_guid += 1
                    if len(sample_no_guid) < DEBUG_SAMPLE_LIMIT:
                        sample_no_guid.append(raw_sale_guid)
                    continue

                detail_item = fetch_sale_detail(sale_guid)
                if not detail_item:
                    skipped_detail_missing += 1
                    register_failed_saleguid(sale_guid)
                    if len(sample_detail_missing) < DEBUG_SAMPLE_LIMIT:
                        sample_detail_missing.append(sale_guid)
                    continue

                data = process_sale(detail_item, bulk_item)
                if not data:
                    skipped_process_sale += 1
                    register_failed_saleguid(sale_guid)
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

                sale_file_id = clean_text(sale_data.get("fileId"))
                sale_url = clean_url(sale_data.get("url"))

                logger.info(
                    "[WORKER-%s][RETRY-%s] saleGuid=%s fileId=%r url=%r",
                    worker_id, retry_round, sale_guid, sale_file_id, sale_url
                )

                sales_rows.append(build_sale_row(sale_data))

                pd = data.get("property", {}) or {}
                if pd:
                    property_rows.append((
                        sale_guid,
                        clean_int(pd.get("streetNumber")),
                        clean_text(pd.get("streetAddress")),
                        clean_text(pd.get("unit")),
                        clean_text(pd.get("direction")),
                        clean_text(pd.get("city")),
                        clean_text(pd.get("county")),
                        clean_text(pd.get("state")),
                        clean_text(pd.get("zip")),
                        clean_int(pd.get("yearBuilt")),
                        clean_int(pd.get("realPropertyTypeId")),
                        clean_int(pd.get("realPropertySubtypeId")),
                    ))

                cd = data.get("commission", {}) or {}
                if cd:
                    commission_rows.append((
                        sale_guid,
                        clean_text(cd.get("transactionCoordinatorName")),
                        clean_text(cd.get("transactionCoordinatorFee")),
                        clean_decimal(cd.get("adminBrokerageComp")),
                        normalize_date(cd.get("dateOfCheck")),
                        normalize_date(cd.get("datePostedToLogBook")),
                        clean_decimal(cd.get("listingCommissionPercent")),
                        clean_decimal(cd.get("listingCommissionAmount")),
                        clean_decimal(cd.get("saleCommissionPercent")),
                        clean_decimal(cd.get("saleCommissionAmount")),
                        clean_decimal(cd.get("otherDeductions")),
                        clean_bool(cd.get("personalDeal")),
                        clean_text(cd.get("commissionBreakdownDetails")),
                        clean_decimal(cd.get("officeGrossCommissionOnSale")),
                    ))

                if fc_guid:
                    file_creator_rows.append((
                        sale_guid,
                        fc_guid,
                        clean_text(fc.get("firstName")),
                        clean_text(fc.get("lastName")),
                        clean_text(fc.get("email")),
                        clean_text(fc.get("alternateEmail")),
                    ))

                for contact in data.get("contacts", []):
                    cguid = clean_text(contact.get("contactGuid"))
                    role = clean_text(contact.get("role"))
                    if cguid and role:
                        contact_rows.append((
                            sale_guid,
                            cguid,
                            role,
                            clean_text(contact.get("firstName")),
                            clean_text(contact.get("lastName")),
                            clean_text(contact.get("phoneNumber")),
                            clean_text(contact.get("email")),
                            clean_text(contact.get("company")),
                            clean_text(contact.get("alternatePhone")),
                            clean_text(contact.get("streetNumber")),
                            clean_text(contact.get("streetName")),
                            clean_text(contact.get("zip")),
                            clean_text(contact.get("city")),
                            clean_text(contact.get("state")),
                            clean_text(contact.get("fax")),
                            clean_text(contact.get("notes")),
                            clean_bool(contact.get("isTrustCompanyOrOtherEntity")),
                            clean_bool(contact.get("isCashDeal")),
                            clean_int(contact.get("loanTypeId")),
                            clean_text(contact.get("loanType")),
                            clean_decimal(contact.get("loanAmount")),
                            clean_int(contact.get("brokerTaxId")),
                            clean_text(contact.get("miscContactType")),
                        ))

                for item in data.get("breakdown", []):
                    name = clean_text(item.get("name"))
                    if name:
                        breakdown_rows.append((
                            sale_guid,
                            name,
                            clean_text(item.get("details")),
                            clean_decimal(item.get("amount")),
                        ))

                for item in data.get("co_agents", []):
                    co_guid = clean_text(item.get("coAgentGuid") or item.get("userGuid"))
                    if co_guid:
                        co_agent_rows.append((sale_guid, co_guid))

                for item in data.get("coordinators", []):
                    contact_guid = clean_text(item.get("contactGuid"))
                    if contact_guid:
                        coordinator_rows.append((
                            sale_guid,
                            contact_guid,
                            clean_text(item.get("firstName")),
                            clean_text(item.get("lastName")),
                            clean_text(item.get("fullName")),
                            clean_text(item.get("email")),
                            clean_text(item.get("phoneNumber") or item.get("phone")),
                            clean_text(item.get("notes")),
                            clean_decimal(item.get("fee") or item.get("tcFee")),
                            clean_bool(item.get("hasAccess")),
                        ))

                for item in data.get("splits", []):
                    agent_guid = clean_text(item.get("agentGuid") or item.get("userGuid"))
                    if agent_guid:
                        split_rows.append((
                            sale_guid,
                            agent_guid,
                            clean_decimal(item.get("amount")),
                            clean_decimal(item.get("percentage")),
                        ))

                rd = data.get("referral", {}) or {}
                if rd:
                    type_obj = rd.get("type", {}) or {}
                    referral_rows.append((
                        sale_guid,
                        clean_int(type_obj.get("id") or rd.get("typeId")),
                        clean_text(type_obj.get("name") or rd.get("typeName")),
                        clean_text(rd.get("contactGuid") or rd.get("agentGuid")),
                        clean_text(rd.get("contactFirstName")),
                        clean_text(rd.get("contactLastName")),
                        clean_text(rd.get("contactEmail")),
                        clean_text(rd.get("contactPhoneNumber")),
                        clean_text(rd.get("brokerageName")),
                        clean_decimal(rd.get("amount")),
                    ))

                emd = data.get("emd", {}) or {}
                if emd:
                    emd_rows.append((
                        sale_guid,
                        clean_bool(emd.get("isEarnestMoneyHeld")),
                        clean_decimal(emd.get("depositAmount")),
                        normalize_date(emd.get("depositDueDate")),
                        normalize_date(emd.get("datePostedToLogBook")),
                        normalize_date(emd.get("dateOfCheck")),
                        clean_decimal(emd.get("additionalDepositAmount")),
                        normalize_date(emd.get("additionalDepositDueDate")),
                    ))

                for item in data.get("activities", []):
                    aid = clean_text(item.get("activityId"))
                    if aid:
                        activity_rows.append((
                            sale_guid,
                            aid,
                            clean_int(item.get("order")),
                            clean_text(item.get("activityName")),
                            normalize_date(item.get("dateAssigned")),
                            clean_int(item.get("typeId")),
                            clean_text(item.get("typeName")),
                            clean_text(item.get("status")),
                            clean_text(item.get("help")),
                            normalize_date(item.get("modifiedOn")),
                        ))

                for item in data.get("docs", []):
                    did = clean_text(item.get("docId"))
                    if did:
                        doc_rows.append((
                            sale_guid,
                            clean_text(item.get("activityId")),
                            did,
                            clean_text(item.get("name")),
                            clean_url(item.get("url")),
                            clean_text(item.get("documentServiceKey")),
                            normalize_date(item.get("modifiedDate")),
                            normalize_date(item.get("uploadDate")),
                            clean_text(item.get("fileName")),
                            clean_text(item.get("extension")),
                            clean_decimal(item.get("fileSize")),
                            clean_int(item.get("pages")),
                        ))

                for item in data.get("activity_docs", []):
                    aid = clean_text(item.get("activityId"))
                    fn = clean_text(item.get("fileName"))
                    if aid and fn:
                        activity_doc_rows.append((sale_guid, aid, fn))

                for item in data.get("comments", []):
                    aid = clean_text(item.get("activityId"))
                    if aid:
                        comment_rows.append((
                            aid,
                            sale_guid,
                            clean_text(item.get("comment")),
                            normalize_date(item.get("createdOn")),
                            clean_text(item.get("createdBy")),
                        ))

                batch_saved += 1

            if sample_no_guid:
                logger.info("[WORKER-%s][RETRY-%s] sample skipped_no_guid: %s", worker_id, retry_round, sample_no_guid)
            if sample_detail_missing:
                logger.info("[WORKER-%s][RETRY-%s] sample skipped_detail_missing: %s", worker_id, retry_round, sample_detail_missing)
            if sample_process_sale_none:
                logger.info("[WORKER-%s][RETRY-%s] sample skipped_process_sale: %s", worker_id, retry_round, sample_process_sale_none)

            if not sales_rows:
                logger.warning(
                    "[WORKER-%s][RETRY-%s] No sales_rows built. batch_size=%s, skipped_no_guid=%s, skipped_detail_missing=%s, skipped_process_sale=%s",
                    worker_id, retry_round, len(sales_batch), skipped_no_guid, skipped_detail_missing, skipped_process_sale
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

            ensure_reference_data(cur, worker_id, users_to_ensure, checklists_to_ensure, offices_to_ensure)

            bulk_execute_values(cur, SALE_UPSERT_SQL, sales_rows_dedup, "sale")
            bulk_execute_values(cur, FILE_CREATOR_UPSERT_SQL, file_creator_rows_dedup, "sale_file_creator")
            bulk_execute_values(cur, PROPERTY_UPSERT_SQL, property_rows_dedup, "sale_property")
            bulk_execute_values(cur, COMMISSION_UPSERT_SQL, commission_rows_dedup, "sale_commission")
            bulk_execute_values(cur, CONTACT_UPSERT_SQL, contact_rows_dedup, "sale_contact")
            bulk_execute_values(cur, CO_AGENT_UPSERT_SQL, co_agent_rows_dedup, "sale_co_agent")
            bulk_execute_values(cur, COORDINATOR_UPSERT_SQL, coordinator_rows_dedup, "sale_transaction_coordinator")
            bulk_execute_values(cur, SPLIT_UPSERT_SQL, split_rows_dedup, "sale_commission_split")
            bulk_execute_values(cur, REFERRAL_UPSERT_SQL, referral_rows_dedup, "sale_commission_referral")
            bulk_execute_values(cur, EMD_UPSERT_SQL, emd_rows_dedup, "sale_earnest_money_deposit")
            bulk_execute_values(cur, ACTIVITY_UPSERT_SQL, activity_rows_dedup, "sale_checklist_activity")
            bulk_execute_values(cur, DOC_UPSERT_SQL, doc_rows_dedup, "sale_checklist_doc")
            bulk_execute_values(cur, ACTIVITY_DOC_UPSERT_SQL, activity_doc_rows_dedup, "sale_checklist_activity_docs")

            if sale_guids_in_batch:
                guid_list = list(sale_guids_in_batch)
                cur.execute("DELETE FROM sale_commission_breakdown WHERE saleGuid = ANY(%s::uuid[])", (guid_list,))
                cur.execute("DELETE FROM sale_checklist_comment WHERE saleGuid = ANY(%s::uuid[])", (guid_list,))
                logger.info(
                    "[WORKER-%s][RETRY-%s] Cleared child rows for sale_commission_breakdown and sale_checklist_comment for %s saleGuids",
                    worker_id, retry_round, len(guid_list)
                )

            bulk_execute_values(cur, BREAKDOWN_INSERT_SQL, breakdown_rows, "sale_commission_breakdown")
            bulk_execute_values(cur, COMMENT_INSERT_SQL, comment_rows, "sale_checklist_comment")

            conn.commit()
            logger.info(
                "[WORKER-%s][RETRY-%s] batch committed successfully. input=%s, valid_sales=%s, skipped_no_guid=%s, skipped_detail_missing=%s, skipped_process_sale=%s, sale_rows=%s, sale_rows_after_dedup=%s",
                worker_id, retry_round, len(sales_batch), batch_saved, skipped_no_guid, skipped_detail_missing,
                skipped_process_sale, len(sales_rows), len(sales_rows_dedup)
            )

            with progress_lock:
                saved_count_global += batch_saved
                processed_count += len(sales_batch)
                if processed_count % 100 == 0:
                    logger.info("Progress: %s sales processed...", processed_count)

    except Exception as e:
        try:
            if 'conn' in locals() and conn:
                conn.rollback()
            logger.error("[WORKER-%s][RETRY-%s] batch rolled back", worker_id, retry_round)
        except Exception:
            logger.exception("[WORKER-%s][RETRY-%s] rollback itself failed", worker_id, retry_round)

        for bulk_item in sales_batch:
            sale_guid = clean_guid(bulk_item.get("saleGuid"))
            if sale_guid:
                register_failed_saleguid(sale_guid)

        with progress_lock:
            error_count_global += len(sales_batch)

        logger.error("[WORKER-%s][RETRY-%s] BATCH ERROR: %s", worker_id, retry_round, e, exc_info=True)

    finally:
        if cur:
            cur.close()


def run_batches(sales: List[Dict[str, Any]], retry_round: int = 0) -> None:
    if not sales:
        return

    batches = [sales[i:i + BATCH_SIZE] for i in range(0, len(sales), BATCH_SIZE)]

    with ThreadPoolExecutor(max_workers=API_DETAIL_WORKERS) as executor:
        futures = [
            executor.submit(process_sale_batch, batch, idx, retry_round)
            for idx, batch in enumerate(batches)
        ]

        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logger.error("RETRY-%s Batch processing error: %s", retry_round, e, exc_info=True)


def retry_failed_saleguids() -> int:
    unresolved_failed_count = 0

    for retry_round in range(1, FAILED_SALEGUID_RETRY_ROUNDS + 1):
        with failed_saleguids_lock:
            current_failed = list(failed_saleguids_global)
            failed_saleguids_global.clear()

        if not current_failed:
            logger.info("No failed saleGuids to retry on round %s.", retry_round)
            return 0

        logger.info("Retry round %s started for %s failed saleGuids.", retry_round, len(current_failed))
        retry_sales = build_retry_sales_from_failed_guids(current_failed)
        run_batches(retry_sales, retry_round=retry_round)

        with failed_saleguids_lock:
            unresolved_failed_count = len(failed_saleguids_global)

        logger.info(
            "Retry round %s completed. unresolved_failed_saleguids=%s",
            retry_round, unresolved_failed_count
        )

        if unresolved_failed_count == 0:
            break

    return unresolved_failed_count


@router.post("/sync-skyslope-sales")
def trigger_sales_sync(db=Depends(get_db)):
    global processed_count, saved_count_global, error_count_global, failed_saleguids_global, detail_cache, last_request_ts

    processed_count = 0
    saved_count_global = 0
    error_count_global = 0
    last_request_ts = 0.0

    with failed_saleguids_lock:
        failed_saleguids_global = set()

    with detail_cache_lock:
        detail_cache = {}


    last_sync_date = get_last_sync_date(db)
    logger.info("Using last_sync_date=%s for this sync run", last_sync_date)

    sales = fetch_sales_bulk(last_sync_date)
    if not sales:
        logger.info("No sales found to sync.")
        return {
            "message": "No sales found to sync.",
            "last_sync_date_used": last_sync_date,
            "saved": 0,
            "errors": 0,
            "failed_saleguids": 0,
        }

    sales = deduplicate_sales_by_guid(sales)
    total_sales = len(sales)

    logger.info("Found %s unique bulk sales to process.", total_sales)

    run_batches(sales, retry_round=0)
    unresolved_failed = retry_failed_saleguids()

    logger.info(
        "Sync completed! total_fetched=%s, saved=%s, errors=%s, unresolved_failed_saleguids=%s",
        total_sales, saved_count_global, error_count_global, unresolved_failed
    )

    if unresolved_failed == 0:
        update_sync_date(db, status="success")
    else:
        logger.warning(
            "Sync date not updated because %s saleGuids still failed after retries.",
            unresolved_failed
        )
        try:
            update_sync_date(
                db,
                status="partial_failure",
                error_message=f"{unresolved_failed} saleGuids unresolved after retries"
            )
        except Exception:
            logger.exception("Failed to persist partial failure sync tracker row")

    return {
        "message": "Sync completed successfully." if unresolved_failed == 0 else "Sync completed with unresolved failures.",
        "last_sync_date_used": last_sync_date,
        "total_fetched": total_sales,
        "saved": saved_count_global,
        "errors": error_count_global,
        "failed_saleguids": unresolved_failed,
    }