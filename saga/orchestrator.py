"""
Saga Orchestrator — Order Fulfillment Saga

Flow:
  OrderPlaced → reserve inventory (real SQL) → authorize payment (simulated)
             → PaymentConfirmed (success)
             → OrderCancelled  (compensation on any failure)

Saga state is persisted in MongoDB `sagas` collection so it survives crashes.
Inventory is managed via direct SQL UPDATE (no Redis involved for that step).
Only the outcome events (InventoryReserved, PaymentAuthorized, etc.) go to Redis.
"""

import os
import sys
import time
import uuid
import random
import psycopg2
from datetime import datetime, timezone

from pymongo import MongoClient, ASCENDING

# Allow importing append_event / get_events_for_order from write-api
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../write-api"))
from events import append_event, get_events_for_order, ConcurrencyError  # noqa: E402

MONGO_URL    = os.getenv("MONGO_URL",    "mongodb://localhost:27017")
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://cqrs:cqrs_pass@localhost:5432/cqrs_read")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _get_pg_conn():
    return psycopg2.connect(POSTGRES_URL)


# ── Simulated payment service ──────────────────────────────────
#
# Mimics a real provider (Stripe, Adyen) that dedupes by Idempotency-Key.
# Calling twice with the same key returns the same result — no double-charge,
# no new auth_code. In production this cache lives in the provider; here it's
# process-local (fine for the simulator, matches the "external system" model).

_PAYMENT_IDEMPOTENCY_CACHE: dict = {}


def simulate_payment_auth(order_id: str, total_amount: float, idempotency_key: str) -> dict:
    if idempotency_key in _PAYMENT_IDEMPOTENCY_CACHE:
        return _PAYMENT_IDEMPOTENCY_CACHE[idempotency_key]

    failure_rate = float(os.getenv("PAYMENT_FAILURE_RATE", "0.15"))
    if random.random() < failure_rate:
        result = {
            "success": False,
            "error":   "card_declined",
            "detail":  f"Payment authorization declined for order {order_id}",
        }
    else:
        auth_code = str(uuid.uuid4())[:8].upper()
        result = {"success": True, "auth_code": auth_code, "amount": total_amount}

    _PAYMENT_IDEMPOTENCY_CACHE[idempotency_key] = result
    return result


# ── Real inventory service (direct SQL) ───────────────────────

def check_and_reserve_inventory(pg_conn, order_id: str, items: list) -> dict:
    """
    Try to reserve qty for every item in a single transaction.
    If any item is out of stock the whole reservation is rolled back.
    Returns {success, reserved_skus} or {success: False, reason}.
    """
    reserved = []
    try:
        with pg_conn.cursor() as cur:
            for item in items:
                cur.execute(
                    """
                    UPDATE inventory
                       SET reserved_qty = reserved_qty + %s
                     WHERE product_id = %s
                       AND (total_qty - reserved_qty) >= %s
                    RETURNING product_id, name
                    """,
                    (item["quantity"], item["product_id"], item["quantity"]),
                )
                row = cur.fetchone()
                if row is None:
                    pg_conn.rollback()
                    return {
                        "success": False,
                        "reason":  f"Insufficient stock for '{item['name']}' (product_id={item['product_id']})",
                    }
                reserved.append(row[0])
        pg_conn.commit()
        return {"success": True, "reserved_skus": reserved}
    except Exception as exc:
        pg_conn.rollback()
        return {"success": False, "reason": str(exc)}


def release_inventory(pg_conn, items: list) -> None:
    """Compensation: undo the reservation for each item."""
    with pg_conn.cursor() as cur:
        for item in items:
            cur.execute(
                "UPDATE inventory SET reserved_qty = reserved_qty - %s WHERE product_id = %s",
                (item["quantity"], item["product_id"]),
            )
    pg_conn.commit()


# ── Saga Orchestrator ──────────────────────────────────────────

class SagaOrchestrator:

    def __init__(self):
        _mongo = MongoClient(MONGO_URL)
        self._col = _mongo["cqrs_events"]["sagas"]
        self._col.create_index([("saga_id",  ASCENDING)], unique=True)
        self._col.create_index([("order_id", ASCENDING)], unique=True)
        self._col.create_index([("state",    ASCENDING)])

    # ── Persistence helpers ───────────────────────────────────

    def create(self, order_id: str, payload: dict) -> dict:
        existing = self._col.find_one({"order_id": order_id})
        if existing:
            existing.pop("_id", None)
            return existing
        doc = {
            "saga_id":    str(uuid.uuid4()),
            "order_id":   order_id,
            "state":      "started",
            "steps":      [],
            "error":      None,
            "payload":    payload,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self._col.insert_one(doc)
        doc.pop("_id", None)
        return doc

    def _load(self, saga_id: str) -> dict:
        doc = self._col.find_one({"saga_id": saga_id})
        if not doc:
            raise ValueError(f"Saga {saga_id} not found")
        doc.pop("_id", None)
        return doc

    def _save(self, saga_id: str, **fields) -> None:
        fields["updated_at"] = _now()
        self._col.update_one({"saga_id": saga_id}, {"$set": fields})

    def _push_step(self, saga_id: str, step: dict) -> None:
        self._col.update_one(
            {"saga_id": saga_id},
            {"$push": {"steps": step}, "$set": {"updated_at": _now()}},
        )

    def _get_order_version(self, order_id: str) -> int:
        events = get_events_for_order(order_id)
        return max((e["version"] for e in events), default=0)

    # ── State machine ─────────────────────────────────────────

    def run(self, saga_id: str) -> None:
        saga = self._load(saga_id)
        state     = saga["state"]
        order_id  = saga["order_id"]
        payload   = saga["payload"]
        items     = payload.get("items", [])
        total_amt = sum(i["quantity"] * i["price"] for i in items)

        if state in ("completed", "failed"):
            print(f"[SAGA] {saga_id[:8]} already {state}, skipping")
            return

        pg_conn = _get_pg_conn()

        try:
            if state == "inventory_reserving":
                existing = [e["event_type"] for e in get_events_for_order(order_id)]
                if "InventoryReserved" in existing:
                    self._save(saga_id, state="inventory_reserved")
                    state = "inventory_reserved"
                elif "InventoryReserveFailed" in existing:
                    self._save(saga_id, state="compensating")
                    state = "compensating"
                else:
                    state = "started"

            if state == "started":
                self._save(saga_id, state="inventory_reserving")
                result = check_and_reserve_inventory(pg_conn, order_id, items)

                if result["success"]:
                    version = self._get_order_version(order_id)
                    try:
                        append_event("InventoryReserved", order_id,
                                     {"reserved_skus": result["reserved_skus"]},
                                     expected_version=version)
                    except ConcurrencyError:
                        pass  # event already appended on a prior attempt
                    self._push_step(saga_id, {
                        "step": "inventory_check", "status": "succeeded",
                        "result": result, "executed_at": _now(),
                    })
                    self._save(saga_id, state="inventory_reserved")
                    print(f"[SAGA] {saga_id[:8]} inventory reserved")
                    delay = float(os.getenv("SAGA_STEP_DELAY_SEC", "0"))
                    if delay > 0:
                        time.sleep(delay)
                    state = "inventory_reserved"

                else:
                    version = self._get_order_version(order_id)
                    try:
                        append_event("InventoryReserveFailed", order_id,
                                     {"reason": result["reason"]},
                                     expected_version=version)
                    except ConcurrencyError:
                        pass
                    self._push_step(saga_id, {
                        "step": "inventory_check", "status": "failed",
                        "error": result["reason"], "executed_at": _now(),
                    })
                    self._save(saga_id, state="compensating")
                    self._compensate(saga_id, order_id, result["reason"], items=None, pg_conn=pg_conn)
                    return

            if state in ("inventory_reserved", "payment_authorizing"):
                self._save(saga_id, state="payment_authorizing")

                existing       = get_events_for_order(order_id)
                existing_types = {e["event_type"]: e for e in existing}

                # ── Idempotent resume: short-circuit if work already done ──
                if "OrderShipped" in existing_types:
                    self._save(saga_id, state="completed")
                    print(f"[SAGA] {saga_id[:8]} already OrderShipped — marking completed")
                    return
                if "PaymentConfirmed" in existing_types and "OrderShipped" not in existing_types:
                    # PaymentConfirmed written but container crashed before OrderShipped — resume from here
                    version = self._get_order_version(order_id)
                    append_event("OrderShipped", order_id, {}, expected_version=version)
                    self._save(saga_id, state="completed")
                    print(f"[SAGA] {saga_id[:8]} resumed — OrderShipped")
                    return
                if "PaymentAuthorizeFailed" in existing_types:
                    reason = existing_types["PaymentAuthorizeFailed"]["payload"].get("reason", "unknown")
                    self._save(saga_id, state="compensating")
                    self._compensate(saga_id, order_id, reason, items=items, pg_conn=pg_conn)
                    return

                if "PaymentAuthorized" in existing_types:
                    # Auth already happened on a previous attempt — reuse it.
                    auth_payload = existing_types["PaymentAuthorized"]["payload"]
                    result = {"success": True,
                              "auth_code": auth_payload["auth_code"],
                              "amount":    auth_payload["amount"]}
                    print(f"[SAGA] {saga_id[:8]} reusing existing PaymentAuthorized (auth_code={result['auth_code']})")
                else:
                    delay = float(os.getenv("SAGA_STEP_DELAY_SEC", "0"))
                    if delay > 0:
                        print(f"[SAGA] {saga_id[:8]} sleeping {delay}s before payment (kill window)")
                        time.sleep(delay)
                    # saga_id is the idempotency key — provider dedupes on it
                    result = simulate_payment_auth(order_id, total_amt, idempotency_key=saga_id)

                if result["success"]:
                    if "PaymentAuthorized" not in existing_types:
                        version = self._get_order_version(order_id)
                        try:
                            append_event("PaymentAuthorized", order_id,
                                         {"auth_code": result["auth_code"], "amount": result["amount"]},
                                         expected_version=version)
                        except ConcurrencyError:
                            pass

                    version = self._get_order_version(order_id)
                    try:
                        append_event("PaymentConfirmed", order_id, {},
                                     expected_version=version)
                    except ConcurrencyError:
                        pass
                    self._push_step(saga_id, {
                        "step": "payment_auth", "status": "succeeded",
                        "result": result, "executed_at": _now(),
                    })
                    delay = float(os.getenv("SAGA_STEP_DELAY_SEC", "0"))
                    if delay > 0:
                        time.sleep(delay)
                    version = self._get_order_version(order_id)
                    append_event("OrderShipped", order_id, {},
                                 expected_version=version)
                    self._save(saga_id, state="completed")
                    print(f"[SAGA] {saga_id[:8]} completed — OrderShipped")

                else:
                    version = self._get_order_version(order_id)
                    try:
                        append_event("PaymentAuthorizeFailed", order_id,
                                     {"reason": result["detail"]},
                                     expected_version=version)
                    except ConcurrencyError:
                        pass
                    self._push_step(saga_id, {
                        "step": "payment_auth", "status": "failed",
                        "error": result["detail"], "executed_at": _now(),
                    })
                    self._save(saga_id, state="compensating")
                    self._compensate(saga_id, order_id, result["detail"],
                                     items=items, pg_conn=pg_conn)

            elif state == "compensating":
                # Resume compensation after a crash
                self._compensate(saga_id, order_id, saga.get("error", "unknown"),
                                 items=items, pg_conn=pg_conn)

        finally:
            pg_conn.close()

    def _compensate(self, saga_id: str, order_id: str, reason: str,
                    items: list | None, pg_conn) -> None:
        if items:
            release_inventory(pg_conn, items)
            print(f"[SAGA] {saga_id[:8]} inventory released (compensation)")

        version = self._get_order_version(order_id)
        try:
            append_event("OrderCancelled", order_id,
                         {"reason": reason, "saga_id": saga_id},
                         expected_version=version)
        except ConcurrencyError:
            pass

        self._save(saga_id, state="failed", error=reason)
        print(f"[SAGA] {saga_id[:8]} failed — OrderCancelled: {reason}")
