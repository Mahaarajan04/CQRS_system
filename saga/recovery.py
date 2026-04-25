"""
Saga Recovery — finds and resumes stuck sagas.

If the saga worker crashes mid-flight (e.g. killed between inventory reservation
and payment authorization), the saga document stays in a non-terminal state
forever. This script finds those sagas and resumes them using the same
SagaOrchestrator.run() logic — idempotent, crash-safe.

Usage:
  python saga/recovery.py                    # recover sagas stuck > 30s (default)
  python saga/recovery.py --timeout-sec 5    # recover sagas stuck > 5s (demo)
  python saga/recovery.py --dry-run          # list stuck sagas without resuming

Demo (in three terminals):

  Terminal 1 — set a kill window so the saga pauses mid-flight:
    docker compose exec saga env SAGA_STEP_DELAY_SEC=15 python saga_worker.py

  Terminal 2 — place an order (saga starts, pauses at payment step):
    curl -s -X POST http://localhost:8001/orders -H 'content-type: application/json' \\
      -d '{"customer_id":"c1","customer_name":"Alice","customer_email":"a@b.com",
           "region":"US-WEST","items":[{"product_id":"p2","name":"Mouse","quantity":1,"price":29.99}]}'

  Terminal 3 — kill the saga worker mid-flight:
    docker compose stop saga

  Wait 10 seconds, then run recovery:
    python saga/recovery.py --timeout-sec 5
"""

import argparse
import os
import sys
from datetime import datetime, timezone, timedelta

from pymongo import MongoClient

# allow importing SagaOrchestrator which in turn imports events.py from write-api
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "saga"))
sys.path.insert(0, os.path.join(ROOT, "write-api"))

from orchestrator import SagaOrchestrator  # noqa: E402

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")

TERMINAL_STATES = {"completed", "failed"}

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


def find_stuck_sagas(col, timeout_sec: int) -> list:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=timeout_sec)).isoformat()
    return list(col.find(
        {
            "state":      {"$nin": list(TERMINAL_STATES)},
            "updated_at": {"$lt": cutoff},
        },
        projection={"_id": 0},
    ))


def recover(timeout_sec: int = 30, dry_run: bool = False):
    mongo = MongoClient(MONGO_URL)
    col   = mongo["cqrs_events"]["sagas"]

    print(f"\n{BOLD}{'='*55}{RESET}")
    print(f"{BOLD}  SAGA RECOVERY{RESET}")
    print(f"{BOLD}{'='*55}{RESET}\n")

    # show all saga states for context
    all_sagas = list(col.find({}, {"state": 1, "_id": 0}))
    state_counts: dict = {}
    for s in all_sagas:
        state_counts[s["state"]] = state_counts.get(s["state"], 0) + 1

    print(f"  All sagas in MongoDB ({len(all_sagas)} total):")
    for state, count in sorted(state_counts.items()):
        marker = f"{GREEN}✓{RESET}" if state in TERMINAL_STATES else f"{RED}⚠{RESET}"
        print(f"    {marker}  {state:<30} {count}")
    print()

    stuck = find_stuck_sagas(col, timeout_sec)

    if not stuck:
        print(f"  {GREEN}No stuck sagas found{RESET} (timeout = {timeout_sec}s).\n")
        return

    print(f"  {RED}Found {len(stuck)} stuck saga(s){RESET} "
          f"(non-terminal, not updated in >{timeout_sec}s):\n")

    for s in stuck:
        age_sec = (
            datetime.now(timezone.utc) -
            datetime.fromisoformat(s["updated_at"])
        ).total_seconds()
        print(f"  {CYAN}{s['saga_id'][:8]}...{RESET}  "
              f"state={s['state']:<30}  "
              f"order={s['order_id'][:8]}  "
              f"stuck={age_sec:.0f}s")
    print()

    if dry_run:
        print(f"  {YELLOW}--dry-run: not resuming.{RESET}\n")
        return

    orchestrator = SagaOrchestrator()
    recovered = 0
    failed    = 0

    for s in stuck:
        saga_id  = s["saga_id"]
        order_id = s["order_id"]
        print(f"  Resuming {saga_id[:8]}... (order {order_id[:8]})  ", end="", flush=True)
        try:
            orchestrator.run(saga_id)
            updated = col.find_one({"saga_id": saga_id}, {"state": 1, "_id": 0})
            new_state = updated["state"] if updated else "unknown"
            if new_state in TERMINAL_STATES:
                print(f"{GREEN}→ {new_state}{RESET}")
                recovered += 1
            else:
                print(f"{YELLOW}→ {new_state} (still running){RESET}")
        except Exception as e:
            print(f"{RED}ERROR: {e}{RESET}")
            failed += 1

    print()
    print(f"  {BOLD}Recovery complete:{RESET}  "
          f"{GREEN}{recovered} recovered{RESET}  "
          f"{RED}{failed} errors{RESET}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Recover stuck sagas")
    parser.add_argument("--timeout-sec", type=int, default=30,
                        help="Resume sagas not updated in this many seconds (default: 30)")
    parser.add_argument("--dry-run", action="store_true",
                        help="List stuck sagas without resuming them")
    args = parser.parse_args()
    recover(timeout_sec=args.timeout_sec, dry_run=args.dry_run)
