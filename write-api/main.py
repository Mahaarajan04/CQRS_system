import uuid
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from events import append_event, get_events_for_order, ConcurrencyError

app = FastAPI(title="CQRS Write API — Command Side")


# ── Request models ────────────────────────────────────────────────────────────

class OrderItem(BaseModel):
    product_id: str
    name: str
    quantity: int
    price: float


class PlaceOrderCommand(BaseModel):
    customer_id: str
    customer_name: str
    customer_email: str
    region: str
    items: List[OrderItem]


class ConfirmPaymentCommand(BaseModel):
    expected_version: int


class ShipOrderCommand(BaseModel):
    expected_version: int
    tracking_number: Optional[str] = None


class CancelOrderCommand(BaseModel):
    expected_version: int
    reason: Optional[str] = None


# ── Command endpoints ─────────────────────────────────────────────────────────

@app.post("/orders", status_code=201)
def place_order(cmd: PlaceOrderCommand):
    """
    PlaceOrder command → writes an OrderPlaced event.
    version starts at 0 (new aggregate), so expected_version is always 0.
    """
    order_id = str(uuid.uuid4())
    total = sum(item.price * item.quantity for item in cmd.items)

    event = append_event(
        event_type="OrderPlaced",
        aggregate_id=order_id,
        expected_version=0,
        payload={
            "customer_id":    cmd.customer_id,
            "customer_name":  cmd.customer_name,
            "customer_email": cmd.customer_email,
            "region":         cmd.region,
            "items":          [i.model_dump() for i in cmd.items],
            "total_amount":   total,
        },
    )
    return {
        "order_id": order_id,
        "event_id": event["event_id"],
        "version":  event["version"],
    }


@app.post("/orders/{order_id}/confirm-payment")
def confirm_payment(order_id: str, cmd: ConfirmPaymentCommand):
    try:
        event = append_event(
            event_type="PaymentConfirmed",
            aggregate_id=order_id,
            expected_version=cmd.expected_version,
            payload={},
        )
        return {"event_id": event["event_id"], "version": event["version"]}
    except ConcurrencyError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/orders/{order_id}/ship")
def ship_order(order_id: str, cmd: ShipOrderCommand):
    try:
        event = append_event(
            event_type="OrderShipped",
            aggregate_id=order_id,
            expected_version=cmd.expected_version,
            payload={"tracking_number": cmd.tracking_number},
        )
        return {"event_id": event["event_id"], "version": event["version"]}
    except ConcurrencyError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/orders/{order_id}/cancel")
def cancel_order(order_id: str, cmd: CancelOrderCommand):
    try:
        event = append_event(
            event_type="OrderCancelled",
            aggregate_id=order_id,
            expected_version=cmd.expected_version,
            payload={"reason": cmd.reason},
        )
        return {"event_id": event["event_id"], "version": event["version"]}
    except ConcurrencyError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ── Diagnostic endpoint ───────────────────────────────────────────────────────

@app.get("/orders/{order_id}/events")
def get_order_event_log(order_id: str):
    """
    Returns the raw event log for an order from MongoDB.
    This is the source of truth — shows event sourcing in action.
    """
    events = get_events_for_order(order_id)
    if not events:
        raise HTTPException(status_code=404, detail="Order not found")
    return events


@app.get("/health")
def health():
    return {"status": "ok"}
