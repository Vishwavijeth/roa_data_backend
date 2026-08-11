import hmac
import base64
import hashlib
import json
import logging
import os
from datetime import datetime
from typing import Dict, Any, Optional
import requests
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from services.account_hold_helper import get_valid_quickbooks_connection
from models.quickbooks import QuickbooksInvoice
from models.brokerage_engine_users import BrokerageEngineUser
from sqlalchemy.orm import Session

from db import get_db

load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter()


# ============== Configuration ==============
QB_WEBHOOK_VERIFIER_TOKEN = os.getenv("QB_WEBHOOK_VERIFIER_TOKEN")
QB_API_BASE_URL = "https://quickbooks.api.intuit.com/v3/company"
QB_MINOR_VERSION = "75"


# ============== Signature Verification ==============

async def verify_webhook_signature(request: Request, verifier_token: str) -> bool:
    """Verify QuickBooks webhook signature using HMAC-SHA256."""
    try:
        intuit_signature = request.headers.get("intuit-signature")

        if not intuit_signature:
            logger.warning("No intuit-signature header found")
            return False

        body = await request.body()

        computed_signature = base64.b64encode(
            hmac.new(
                verifier_token.encode("utf-8"),
                body,
                hashlib.sha256
            ).digest()
        ).decode("utf-8")

        return hmac.compare_digest(intuit_signature, computed_signature)

    except Exception as e:
        logger.error(f"Signature verification error: {e}")
        return False


# ============== QuickBooks API ==============

def fetch_invoice_from_quickbooks(
    realm_id: str,
    access_token: str,
    invoice_id: str
) -> Optional[Dict[str, Any]]:
    """Fetch invoice details from QuickBooks API."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    invoice_url = f"{QB_API_BASE_URL}/{realm_id}/invoice/{invoice_id}"

    try:
        resp = requests.get(
            invoice_url,
            headers=headers,
            params={"minorversion": QB_MINOR_VERSION},
            timeout=30,
        )
        if resp.status_code == 404:
            logger.warning(f"Invoice {invoice_id} not found in QuickBooks (404)")
            return None
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch invoice {invoice_id}: {str(e)}")
        return None

    return resp.json().get("Invoice")


def parse_invoice(invoice: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse QuickBooks invoice response into table-ready format."""
    try:
        invoice_id = str(invoice.get("Id"))
        sync_token = int(invoice.get("SyncToken", 0))

        customer_ref = invoice.get("CustomerRef", {})
        customer_id = str(customer_ref.get("value")) if customer_ref.get("value") else None

        balance = float(invoice.get("Balance", 0))
        total_amt = float(invoice.get("TotalAmt", 0))

        due_date_str = invoice.get("DueDate")
        due_date = None
        if due_date_str:
            try:
                due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()
            except ValueError:
                due_date = None

        txn_date_str = invoice.get("TxnDate")
        txn_date = None
        if txn_date_str:
            try:
                txn_date = datetime.strptime(txn_date_str, "%Y-%m-%d").date()
            except ValueError:
                txn_date = None

        doc_number = invoice.get("DocNumber")

        return {
            "invoice_id": invoice_id,
            "customer_id": customer_id,
            "sync_token": sync_token,
            "balance": balance,
            "total_amt": total_amt,
            "due_date": due_date,
            "txn_date": txn_date,
            "doc_number": doc_number,
            "updated_at": datetime.now(),
        }
    except (KeyError, ValueError, TypeError) as e:
        logger.error(f"Error parsing invoice {invoice.get('Id', 'unknown')}: {e}")
        return None


# ============== Webhook Processing ==============

def process_invoice_event(
    db: Session,
    qb_connection: Dict[str, Any],
    invoice_id: str,
    operation: str
) -> Dict[str, Any]:

    result = {
        "invoice_id": invoice_id,
        "operation": operation,
        "success": False,
        "message": None,
        "customer_checked": False,
        "customer_exists": False,
    }

    try:
        # -------------------- DELETE --------------------
        if operation == "delete":
            existing_invoice = db.query(QuickbooksInvoice).filter(
                QuickbooksInvoice.invoice_id == invoice_id
            ).first()

            if existing_invoice:
                db.delete(existing_invoice)
                db.commit()
                logger.info(f"✓ Deleted invoice {invoice_id}")
                result["success"] = True
                result["message"] = f"Invoice {invoice_id} deleted successfully"
            else:
                result["message"] = f"Invoice {invoice_id} not found, nothing to delete"
                result["success"] = True

            return result

        # -------------------- UPDATE --------------------
        if operation == "update":
            existing_invoice = db.query(QuickbooksInvoice).filter(
                QuickbooksInvoice.invoice_id == invoice_id
            ).first()

            if not existing_invoice:
                logger.info(
                    f"Invoice {invoice_id} not found for update — "
                    f"falling back to create-style insert"
                )
                operation = "create"  # fall through to create logic below
            else:
                logger.info(f"Fetching invoice {invoice_id} from QuickBooks for update...")
                invoice = fetch_invoice_from_quickbooks(
                    realm_id=qb_connection["realm_id"],
                    access_token=qb_connection["access_token"],
                    invoice_id=invoice_id,
                )

                if not invoice:
                    result["message"] = f"Failed to fetch invoice {invoice_id} for update"
                    return result

                invoice_data = parse_invoice(invoice)
                if not invoice_data:
                    result["message"] = f"Failed to parse invoice {invoice_id}"
                    return result

                existing_invoice.customer_id = invoice_data["customer_id"]
                existing_invoice.sync_token = invoice_data["sync_token"]
                existing_invoice.balance = invoice_data["balance"]
                existing_invoice.total_amt = invoice_data["total_amt"]
                existing_invoice.due_date = invoice_data["due_date"]
                existing_invoice.doc_number = invoice_data["doc_number"]
                existing_invoice.txn_date = invoice_data["txn_date"]
                existing_invoice.updated_at = invoice_data["updated_at"]

                db.commit()
                logger.info(f"✓ Updated invoice {invoice_id}")

                result["success"] = True
                result["message"] = f"Invoice {invoice_id} updated successfully"
                return result

        # -------------------- CREATE --------------------
        if operation == "create":
            logger.info(f"Fetching invoice {invoice_id} from QuickBooks...")
            invoice = fetch_invoice_from_quickbooks(
                realm_id=qb_connection["realm_id"],
                access_token=qb_connection["access_token"],
                invoice_id=invoice_id,
            )

            if not invoice:
                result["message"] = f"Failed to fetch invoice {invoice_id}"
                return result

            invoice_data = parse_invoice(invoice)
            if not invoice_data:
                result["message"] = f"Failed to parse invoice {invoice_id}"
                return result

            customer_id = invoice_data["customer_id"]
            result["customer_checked"] = True
            result["customer_exists"] = db.query(BrokerageEngineUser).filter(
                BrokerageEngineUser.qb_customerid == str(customer_id)
            ).first() is not None

            logger.info(f"Customer ID: {customer_id}, Exists in system: {result['customer_exists']}")

            if not result["customer_exists"]:
                result["message"] = f"Customer {customer_id} not found in brokerage_engine_users, skipping"
                result["success"] = True
                return result

            existing_invoice = db.query(QuickbooksInvoice).filter(
                QuickbooksInvoice.invoice_id == invoice_id
            ).first()

            if existing_invoice:
                result["message"] = f"Invoice {invoice_id} already exists, skipping"
                result["success"] = True
                return result

            new_invoice = QuickbooksInvoice(
                invoice_id=invoice_data["invoice_id"],
                customer_id=invoice_data["customer_id"],
                sync_token=invoice_data["sync_token"],
                balance=invoice_data["balance"],
                total_amt=invoice_data["total_amt"],
                due_date=invoice_data["due_date"],
                doc_number=invoice_data["doc_number"],
                txn_date=invoice_data["txn_date"],
                updated_at=invoice_data["updated_at"],
            )

            db.add(new_invoice)
            db.commit()
            logger.info(f"✓ Inserted invoice {invoice_data['invoice_id']}")

            result["success"] = True
            result["message"] = f"Invoice {invoice_id} created successfully"
            return result

        result["message"] = f"Unknown operation: {operation}"
        return result

    except Exception as e:
        db.rollback()
        logger.error(f"Error processing invoice: {str(e)}")
        result["message"] = f"Error processing invoice: {str(e)}"
        return result


# ============== Webhook Endpoint ==============

@router.post("/webhooks/quickbooks")
async def quickbooks_invoice_webhook(request: Request):
    """QuickBooks Invoice Webhook Endpoint — legacy eventNotifications format."""
    print("webhook :)")

    if QB_WEBHOOK_VERIFIER_TOKEN:
        is_valid = await verify_webhook_signature(request, QB_WEBHOOK_VERIFIER_TOKEN)

        if not is_valid:
            logger.error("Invalid webhook signature")
            raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    logger.info("========== QUICKBOOKS WEBHOOK PAYLOAD START ==========")
    logger.info(json.dumps(payload, indent=2, default=str))
    logger.info("=========== QUICKBOOKS WEBHOOK PAYLOAD END ===========")

    event_notifications = payload.get("eventNotifications", [])
    logger.info(f"QuickBooks Invoice Webhook Received - Notifications: {len(event_notifications)}")

    db = next(get_db())
    conn = db.connection().connection

    qb_connection = await get_valid_quickbooks_connection(conn)

    if not qb_connection:
        logger.error("Failed to get QuickBooks connection")
        raise HTTPException(status_code=500, detail="QuickBooks connection not available")

    logger.info(f"Using QuickBooks Realm: {qb_connection['realm_id']}")

    try:
        results = []

        for notification in event_notifications:
            realm_id = notification.get("realmId")

            if realm_id != qb_connection["realm_id"]:
                logger.warning(
                    f"Notification realmId {realm_id} does not match "
                    f"configured realm {qb_connection['realm_id']} — skipping"
                )
                continue

            entities = notification.get("dataChangeEvent", {}).get("entities", [])

            for entity in entities:
                entity_name = entity.get("name", "")
                entity_id = entity.get("id")
                operation = entity.get("operation", "").lower()  # "Update" -> "update"

                if entity_name != "Invoice":
                    logger.info(f"Skipping {entity_name} event")
                    continue

                if not entity_id:
                    logger.warning("No entity id in event, skipping")
                    continue

                logger.info(f"Processing Invoice {operation.upper()} - ID: {entity_id}")

                result = process_invoice_event(
                    db=db,
                    qb_connection=qb_connection,
                    invoice_id=entity_id,
                    operation=operation,
                )

                results.append(result)
                logger.info(f"Result: {result['message']}")

        successful = sum(1 for r in results if r["success"])
        failed = sum(1 for r in results if not r["success"])
        skipped = sum(
            1 for r in results
            if r["success"] and not r.get("customer_exists", True)
        )

        logger.info(
            f"Summary - Total: {len(results)}, "
            f"Success: {successful}, Failed: {failed}, Skipped: {skipped}"
        )

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Webhook received",
                "processed": len(results),
                "successful": successful,
                "failed": failed,
                "skipped": skipped,
            }
        )

    except Exception as e:
        logger.exception(f"Webhook processing error: {str(e)}")
        raise

    finally:
        pass