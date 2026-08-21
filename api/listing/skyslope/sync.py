import json
import logging
import time
import requests
from typing import Any, Dict, List, Optional, Sequence, Set
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db import get_db
from services.session import get_session_token
from services.skyslope_sync.utils import (
    normalize_date, clean_bool, clean_decimal, 
    clean_guid, clean_int, clean_text, 
    clean_url, to_json_text, get_last_sync_date, update_sync_date
)

# Import SkySlope ORM Models
from models.skyslope.meta import Office, Checklist, Stage
from models.skyslope.users import SkyslopeUser
from models.skyslope.sale import Sale, SaleFileCreator
from models.skyslope.property import SaleProperty
from models.skyslope.contact import SaleContact, SaleCoAgent, SaleTransactionCoordinator
from models.skyslope.commission import (
    SaleCommission, SaleCommissionBreakdown, SaleCommissionSplit, SaleCommissionReferral
)
from models.skyslope.earnest_money_deposit import SaleEarnestMoneyDeposit
from models.skyslope.checklist import (
    SaleChecklistActivity, SaleChecklistDoc, SaleChecklistActivityDocs, SaleChecklistComment
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger(__name__)

router = APIRouter()

SALES_BASE_URL = "https://api.skyslope.com/api/files"
SALE_DETAIL_URL_TEMPLATE = "https://api.skyslope.com/api/files/sales/{saleGuid}"
SALES_FILTER_TYPE = "sale"

REQUEST_TIMEOUT = 300
HTTP_RETRY_TOTAL = 3
HTTP_BACKOFF_FACTOR = 1
API_DETAIL_WORKERS = 2
BATCH_SIZE = 50
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
failed_saleguids_global: Set[str] = set()
detail_cache: Dict[str, Dict[str, Any]] = {}
last_request_ts = 0.0

HTTP_SESSION = requests.Session()


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
                logger.warning("SkySlope 429 for url=%s attempt=%s sleeping %.2fs", url, attempt, sleep_for)
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
        HTTP_SESSION.headers.update({"Session": token, "Content-Type": "application/json"})
    except Exception as e:
        logger.error("Failed to obtain session token from services/session.py: %s", e)
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
    return list(latest_by_guid.values())


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
            entries = [entries]
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


def register_failed_saleguid(sale_guid: str) -> None:
    if not sale_guid:
        return
    with failed_saleguids_lock:
        failed_saleguids_global.add(sale_guid)


def upsert_orm_records(db: Session, model_cls: Any, records: List[Dict[str, Any]]) -> None:
    """
    Executes PostgreSQL batch upsert (ON CONFLICT DO UPDATE) mapped directly to SQLAlchemy ORM Model.
    """
    if not records:
        return

    table = model_cls.__table__
    pk_cols = [c.name for c in table.primary_key.columns]

    # Deduplicate in-memory by Primary Keys
    dedup_map = {}
    for rec in records:
        pk_val = tuple(rec.get(k) for k in pk_cols)
        dedup_map[pk_val] = rec
    unique_records = list(dedup_map.values())

    stmt = pg_insert(table).values(unique_records)

    update_cols = {
        c.name: getattr(stmt.excluded, c.name)
        for c in table.columns
        if c.name not in pk_cols
    }

    if update_cols:
        stmt = stmt.on_conflict_do_update(
            index_elements=pk_cols,
            set_=update_cols
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=pk_cols)

    db.execute(stmt)


def process_sale_batch_orm(sales_batch: List[Dict[str, Any]], worker_id: int, retry_round: int = 0) -> None:
    global processed_count, saved_count_global, error_count_global

    db: Session = next(get_db())

    try:
        users_to_ensure = set()
        offices_to_ensure: Dict[str, Optional[str]] = {}
        checklists_to_ensure: Dict[int, Optional[str]] = {}
        sale_guids_in_batch: Set[str] = set()

        sales_dicts = []
        property_dicts = []
        commission_dicts = []
        file_creator_dicts = []
        contact_dicts = []
        breakdown_dicts = []
        co_agent_dicts = []
        coordinator_dicts = []
        split_dicts = []
        referral_dicts = []
        emd_dicts = []
        activity_dicts = []
        doc_dicts = []
        activity_doc_dicts = []
        comment_dicts = []

        batch_saved = 0

        for bulk_item in sales_batch:
            raw_sale_guid = bulk_item.get("saleGuid")
            sale_guid = clean_guid(raw_sale_guid)

            if not sale_guid:
                continue

            detail_item = fetch_sale_detail(sale_guid)
            if not detail_item:
                register_failed_saleguid(sale_guid)
                continue

            data = process_sale(detail_item, bulk_item)
            if not data:
                register_failed_saleguid(sale_guid)
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

            sales_dicts.append({
                "transaction_type": clean_text(sale_data.get("objectType")),
                "saleguid": sale_guid,
                "listingguid": clean_text(sale_data.get("listingGuid")),
                "agentguid": clean_text(sale_data.get("agentGuid")),
                "createdbyguid": clean_text(sale_data.get("createdByGuid")),
                "mlsnumber": clean_text(sale_data.get("mlsNumber")),
                "email": clean_text(sale_data.get("portalEmail") or sale_data.get("email")),
                "statusid": clean_int(sale_data.get("statusId")),
                "status": clean_text(sale_data.get("status")),
                "officeguid": clean_text(sale_data.get("officeGuid")),
                "checklisttypeid": clean_int(sale_data.get("checklistTypeId")),
                "escrownumber": clean_text(sale_data.get("escrowNumber")),
                "escrowclosingdate": normalize_date(sale_data.get("escrowClosingDate")),
                "actualclosingdate": normalize_date(sale_data.get("actualClosingDate")),
                "contractacceptancedate": normalize_date(sale_data.get("contractAcceptanceDate")),
                "createdon": normalize_date(sale_data.get("createdOn")),
                "checklistmodifiedon": normalize_date(sale_data.get("checklistModifiedOn")),
                "deaddate": normalize_date(sale_data.get("deadDate")),
                "reviewerguid": clean_text(sale_data.get("reviewerGuid")),
                "sourceid": clean_int(sale_data.get("sourceId")),
                "source": clean_text(sale_data.get("source")),
                "othersource": clean_text(sale_data.get("otherSource")),
                "dealtype": clean_text(sale_data.get("dealType")),
                "saletypeid": clean_int(sale_data.get("saleTypeId")),
                "listingprice": clean_decimal(sale_data.get("listingPrice")),
                "saleprice": clean_decimal(sale_data.get("salePrice")),
                "isofficelead": clean_bool(sale_data.get("isOfficeLead")),
                "cobrokercompany": clean_text(sale_data.get("coBrokerCompany")),
                "realpropertytype": clean_text(sale_data.get("realPropertyType")),
                "realpropertysubtype": clean_text(sale_data.get("realPropertySubtype")),
                "commerciallease": clean_text(sale_data.get("commercialLease")),
                "stageid": clean_int(sale_data.get("stageId")),
                "customfields": custom_fields,
                "fileid": clean_text(sale_data.get("fileId")),
                "url": clean_url(sale_data.get("url")),
            })

            pd = data.get("property", {}) or {}
            if pd:
                property_dicts.append({
                    "saleguid": sale_guid,
                    "streetnumber": clean_int(pd.get("streetNumber")),
                    "streetaddress": clean_text(pd.get("streetAddress")),
                    "unit": clean_text(pd.get("unit")),
                    "direction": clean_text(pd.get("direction")),
                    "city": clean_text(pd.get("city")),
                    "county": clean_text(pd.get("county")),
                    "state": clean_text(pd.get("state")),
                    "zip": clean_text(pd.get("zip")),
                    "yearbuilt": clean_int(pd.get("yearBuilt")),
                    "realpropertytypeid": clean_int(pd.get("realPropertyTypeId")),
                    "realpropertysubtypeid": clean_int(pd.get("realPropertySubtypeId")),
                })

            cd = data.get("commission", {}) or {}
            if cd:
                commission_dicts.append({
                    "saleguid": sale_guid,
                    "transactioncoordinatorname": clean_text(cd.get("transactionCoordinatorName")),
                    "transactioncoordinatorfee": clean_text(cd.get("transactionCoordinatorFee")),
                    "adminbrokeragecomp": clean_decimal(cd.get("adminBrokerageComp")),
                    "dateofcheck": normalize_date(cd.get("dateOfCheck")),
                    "datepostedtologbook": normalize_date(cd.get("datePostedToLogBook")),
                    "listingcommissionpercent": clean_decimal(cd.get("listingCommissionPercent")),
                    "listingcommissionamount": clean_decimal(cd.get("listingCommissionAmount")),
                    "salecommissionpercent": clean_decimal(cd.get("saleCommissionPercent")),
                    "salecommissionamount": clean_decimal(cd.get("saleCommissionAmount")),
                    "otherdeductions": clean_decimal(cd.get("otherDeductions")),
                    "personaldeal": clean_bool(cd.get("personalDeal")),
                    "commissionbreakdowndetails": clean_text(cd.get("commissionBreakdownDetails")),
                    "officegrosscommissiononsale": clean_decimal(cd.get("officeGrossCommissionOnSale")),
                })

            if fc_guid:
                file_creator_dicts.append({
                    "saleguid": sale_guid,
                    "guid": fc_guid,
                    "firstname": clean_text(fc.get("firstName")),
                    "lastname": clean_text(fc.get("lastName")),
                    "email": clean_text(fc.get("email")),
                    "alternateemail": clean_text(fc.get("alternateEmail")),
                })

            for contact in data.get("contacts", []):
                cguid = clean_text(contact.get("contactGuid"))
                role = clean_text(contact.get("role"))
                if cguid and role:
                    contact_dicts.append({
                        "saleguid": sale_guid,
                        "contactguid": cguid,
                        "role": role,
                        "firstname": clean_text(contact.get("firstName")),
                        "lastname": clean_text(contact.get("lastName")),
                        "phonenumber": clean_text(contact.get("phoneNumber")),
                        "email": clean_text(contact.get("email")),
                        "company": clean_text(contact.get("company")),
                        "alternatephone": clean_text(contact.get("alternatePhone")),
                        "streetnumber": clean_text(contact.get("streetNumber")),
                        "streetname": clean_text(contact.get("streetName")),
                        "zip": clean_text(contact.get("zip")),
                        "city": clean_text(contact.get("city")),
                        "state": clean_text(contact.get("state")),
                        "fax": clean_text(contact.get("fax")),
                        "notes": clean_text(contact.get("notes")),
                        "istrustcompanyorotherentity": clean_bool(contact.get("isTrustCompanyOrOtherEntity")),
                        "iscashdeal": clean_bool(contact.get("isCashDeal")),
                        "loantypeid": clean_int(contact.get("loanTypeId")),
                        "loantype": clean_text(contact.get("loanType")),
                        "loanamount": clean_decimal(contact.get("loanAmount")),
                        "brokertaxid": clean_int(contact.get("brokerTaxId")),
                        "misccontacttype": clean_text(contact.get("miscContactType")),
                    })

            for item in data.get("breakdown", []):
                name = clean_text(item.get("name"))
                if name:
                    breakdown_dicts.append({
                        "saleguid": sale_guid,
                        "name": name,
                        "details": clean_text(item.get("details")),
                        "amount": clean_decimal(item.get("amount")),
                    })

            for item in data.get("co_agents", []):
                co_guid = clean_text(item.get("coAgentGuid") or item.get("userGuid"))
                if co_guid:
                    co_agent_dicts.append({
                        "saleguid": sale_guid,
                        "coagentguid": co_guid
                    })

            for item in data.get("coordinators", []):
                contact_guid = clean_text(item.get("contactGuid"))
                if contact_guid:
                    coordinator_dicts.append({
                        "saleguid": sale_guid,
                        "contactguid": contact_guid,
                        "firstname": clean_text(item.get("firstName")),
                        "lastname": clean_text(item.get("lastName")),
                        "fullname": clean_text(item.get("fullName")),
                        "email": clean_text(item.get("email")),
                        "phonenumber": clean_text(item.get("phoneNumber") or item.get("phone")),
                        "notes": clean_text(item.get("notes")),
                        "fee": clean_decimal(item.get("fee") or item.get("tcFee")),
                        "hasaccess": clean_bool(item.get("hasAccess")),
                    })

            for item in data.get("splits", []):
                agent_guid = clean_text(item.get("agentGuid") or item.get("userGuid"))
                if agent_guid:
                    split_dicts.append({
                        "saleguid": sale_guid,
                        "agentguid": agent_guid,
                        "amount": clean_decimal(item.get("amount")),
                        "percentage": clean_decimal(item.get("percentage")),
                    })

            rd = data.get("referral", {}) or {}
            if rd:
                type_obj = rd.get("type", {}) or {}
                referral_dicts.append({
                    "saleguid": sale_guid,
                    "typeid": clean_int(type_obj.get("id") or rd.get("typeId")),
                    "typename": clean_text(type_obj.get("name") or rd.get("typeName")),
                    "contactguid": clean_text(rd.get("contactGuid") or rd.get("agentGuid")),
                    "contactfirstname": clean_text(rd.get("contactFirstName")),
                    "contactlastname": clean_text(rd.get("contactLastName")),
                    "contactemail": clean_text(rd.get("contactEmail")),
                    "contactphonenumber": clean_text(rd.get("contactPhoneNumber")),
                    "brokeragename": clean_text(rd.get("brokerageName")),
                    "amount": clean_decimal(rd.get("amount")),
                })

            emd = data.get("emd", {}) or {}
            if emd:
                emd_dicts.append({
                    "saleguid": sale_guid,
                    "isearnestmoneyheld": clean_bool(emd.get("isEarnestMoneyHeld")),
                    "depositamount": clean_decimal(emd.get("depositAmount")),
                    "depositduedate": normalize_date(emd.get("depositDueDate")),
                    "datepostedtologbook": normalize_date(emd.get("datePostedToLogBook")),
                    "dateofcheck": normalize_date(emd.get("dateOfCheck")),
                    "additionaldepositamount": clean_decimal(emd.get("additionalDepositAmount")),
                    "additionaldepositduedate": normalize_date(emd.get("additionalDepositDueDate")),
                })

            for item in data.get("activities", []):
                aid = clean_text(item.get("activityId"))
                if aid:
                    activity_dicts.append({
                        "saleguid": sale_guid,
                        "activityid": aid,
                        "order": clean_int(item.get("order")),
                        "activityname": clean_text(item.get("activityName")),
                        "dateassigned": normalize_date(item.get("dateAssigned")),
                        "typeid": clean_int(item.get("typeId")),
                        "typename": clean_text(item.get("typeName")),
                        "status": clean_text(item.get("status")),
                        "help": clean_text(item.get("help")),
                        "modifiedon": normalize_date(item.get("modifiedOn")),
                    })

            for item in data.get("docs", []):
                did = clean_text(item.get("docId"))
                if did:
                    doc_dicts.append({
                        "docid": did,
                        "saleguid": sale_guid,
                        "activityid": clean_text(item.get("activityId")),
                        "name": clean_text(item.get("name")),
                        "url": clean_url(item.get("url")),
                        "documentservicekey": clean_text(item.get("documentServiceKey")),
                        "modifieddate": normalize_date(item.get("modifiedDate")),
                        "uploaddate": normalize_date(item.get("uploadDate")),
                        "filename": clean_text(item.get("fileName")),
                        "extension": clean_text(item.get("extension")),
                        "filesize": clean_decimal(item.get("fileSize")),
                        "pages": clean_int(item.get("pages")),
                    })

            for item in data.get("activity_docs", []):
                aid = clean_text(item.get("activityId"))
                fn = clean_text(item.get("fileName"))
                if aid and fn:
                    activity_doc_dicts.append({
                        "saleguid": sale_guid,
                        "activityid": aid,
                        "filename": fn
                    })

            for item in data.get("comments", []):
                aid = clean_text(item.get("activityId"))
                if aid:
                    comment_dicts.append({
                        "activityid": aid,
                        "saleguid": sale_guid,
                        "comment": clean_text(item.get("comment")),
                        "createdon": normalize_date(item.get("createdOn")),
                        "createdby": clean_text(item.get("createdBy")),
                    })

            batch_saved += 1

        if not sales_dicts:
            return

        # Ensure Reference Data in ORM
        if users_to_ensure:
            upsert_orm_records(db, SkyslopeUser, [{"userguid": u} for u in users_to_ensure])
        if checklists_to_ensure:
            upsert_orm_records(db, Checklist, [{"typeid": k, "typename": v} for k, v in checklists_to_ensure.items()])
        if offices_to_ensure:
            upsert_orm_records(db, Office, [{"officeguid": k, "officename": v} for k, v in offices_to_ensure.items()])

        # Perform SQLAlchemy ORM batch upserts
        upsert_orm_records(db, Sale, sales_dicts)
        upsert_orm_records(db, SaleFileCreator, file_creator_dicts)
        upsert_orm_records(db, SaleProperty, property_dicts)
        upsert_orm_records(db, SaleCommission, commission_dicts)
        upsert_orm_records(db, SaleContact, contact_dicts)
        upsert_orm_records(db, SaleCoAgent, co_agent_dicts)
        upsert_orm_records(db, SaleTransactionCoordinator, coordinator_dicts)
        upsert_orm_records(db, SaleCommissionSplit, split_dicts)
        upsert_orm_records(db, SaleCommissionReferral, referral_dicts)
        upsert_orm_records(db, SaleEarnestMoneyDeposit, emd_dicts)
        upsert_orm_records(db, SaleChecklistActivity, activity_dicts)
        upsert_orm_records(db, SaleChecklistDoc, doc_dicts)
        upsert_orm_records(db, SaleChecklistActivityDocs, activity_doc_dicts)

        # Clear child collections for the batch of sales and insert fresh records
        if sale_guids_in_batch:
            guid_list = list(sale_guids_in_batch)
            db.query(SaleCommissionBreakdown).filter(SaleCommissionBreakdown.saleguid.in_(guid_list)).delete(synchronize_session=False)
            db.query(SaleChecklistComment).filter(SaleChecklistComment.saleguid.in_(guid_list)).delete(synchronize_session=False)

        if breakdown_dicts:
            db.bulk_insert_mappings(SaleCommissionBreakdown, breakdown_dicts)

        if comment_dicts:
            db.bulk_insert_mappings(SaleChecklistComment, comment_dicts)

        db.commit()

        with progress_lock:
            saved_count_global += batch_saved
            processed_count += len(sales_batch)

    except Exception as e:
        db.rollback()
        logger.error("[WORKER-%s][RETRY-%s] ORM Batch transaction rolled back: %s", worker_id, retry_round, e, exc_info=True)
        for bulk_item in sales_batch:
            sale_guid = clean_guid(bulk_item.get("saleGuid"))
            if sale_guid:
                register_failed_saleguid(sale_guid)
        with progress_lock:
            error_count_global += len(sales_batch)
    finally:
        db.close()


def run_batches(sales: List[Dict[str, Any]], retry_round: int = 0) -> None:
    if not sales:
        return

    batches = [sales[i:i + BATCH_SIZE] for i in range(0, len(sales), BATCH_SIZE)]

    with ThreadPoolExecutor(max_workers=API_DETAIL_WORKERS) as executor:
        futures = [
            executor.submit(process_sale_batch_orm, batch, idx, retry_round)
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
            return 0

        retry_sales = [{"saleGuid": guid} for guid in current_failed if clean_guid(guid)]
        run_batches(retry_sales, retry_round=retry_round)

        with failed_saleguids_lock:
            unresolved_failed_count = len(failed_saleguids_global)

        if unresolved_failed_count == 0:
            break

    return unresolved_failed_count


@router.post("/sync-skyslope-sales-orm")
def trigger_sales_sync(db: Session = Depends(get_db)):
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
    logger.info("Using last_sync_date=%s for SkySlope ORM sync run", last_sync_date)

    sales = fetch_sales_bulk(last_sync_date)
    if not sales:
        return {
            "message": "No sales found to sync.",
            "last_sync_date_used": last_sync_date,
            "saved": 0,
            "errors": 0,
            "failed_saleguids": 0,
        }

    sales = deduplicate_sales_by_guid(sales)
    total_sales = len(sales)

    logger.info("Found %s unique bulk sales to process via SQLAlchemy ORM.", total_sales)

    run_batches(sales, retry_round=0)
    unresolved_failed = retry_failed_saleguids()

    if unresolved_failed == 0:
        update_sync_date(db, status="success")
    else:
        try:
            update_sync_date(
                db,
                status="partial_failure",
                error_message=f"{unresolved_failed} saleGuids unresolved after retries"
            )
        except Exception:
            logger.exception("Failed to persist partial failure sync tracker row")

    return {
        "message": "Sync completed successfully via ORM." if unresolved_failed == 0 else "Sync completed with unresolved failures via ORM.",
        "last_sync_date_used": last_sync_date,
        "total_fetched": total_sales,
        "saved": saved_count_global,
        "errors": error_count_global,
        "failed_saleguids": unresolved_failed,
    }