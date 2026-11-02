"""Local store for Microsoft billing orders. Raw objects are never filtered."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "raw"
DERIVED_DIR = ROOT / "derived"
ORDERS_PATH = RAW_DIR / "orders.json"
STATE_PATH = RAW_DIR / "state.json"
META_PATH = RAW_DIR / "meta.json"
FETCH_LOG_PATH = RAW_DIR / "fetch_log.jsonl"
RULES_PATH = ROOT / "rules.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dirs() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def load_orders() -> dict[str, dict[str, Any]]:
    data = load_json(ORDERS_PATH, {"version": 1, "orders": {}})
    orders = data.get("orders") or {}
    if isinstance(orders, list):
        return {o["orderId"]: o for o in orders if o.get("orderId")}
    return dict(orders)


def save_orders(orders: dict[str, dict[str, Any]]) -> None:
    save_json(ORDERS_PATH, {"version": 1, "count": len(orders), "orders": orders})


def load_meta() -> dict[str, Any]:
    return load_json(META_PATH, {})


def save_meta(meta: dict[str, Any]) -> None:
    save_json(META_PATH, meta)


def load_state() -> dict[str, Any]:
    return load_json(
        STATE_PATH,
        {
            "period": "SevenYears",
            "order_type_filter": "All",
            "complete": False,
            "order_count": 0,
            "last_fetch_at": None,
            "last_fetch_mode": None,
            "newest_order_id": None,
            "resume_continuation_token": None,
        },
    )


def save_state(state: dict[str, Any]) -> None:
    save_json(STATE_PATH, state)


def append_fetch_log(entry: dict[str, Any]) -> None:
    FETCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FETCH_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def upsert_orders(
    store: dict[str, dict[str, Any]],
    incoming: list[dict[str, Any]],
    *,
    source: str,
    meta: dict[str, Any],
    fetched_at: str | None = None,
) -> tuple[int, int]:
    """Insert or replace by orderId. Returns (new_count, updated_count)."""
    fetched_at = fetched_at or utc_now()
    new_n = 0
    upd_n = 0
    for order in incoming:
        oid = order.get("orderId")
        if not oid:
            continue
        prev = store.get(oid)
        info = meta.get(oid) or {}
        if prev is None:
            new_n += 1
            info["first_seen"] = fetched_at
        elif prev != order:
            upd_n += 1
        else:
            info["last_seen"] = fetched_at
            info.setdefault("source", source)
            meta[oid] = info
            continue
        store[oid] = order
        info["updated"] = fetched_at
        info["last_seen"] = fetched_at
        info["source"] = source
        meta[oid] = info
    return new_n, upd_n


def refresh_state(state: dict[str, Any], store: dict[str, dict[str, Any]], **extra: Any) -> dict[str, Any]:
    state = dict(state)
    state["order_count"] = len(store)
    state.update(extra)
    newest = None
    newest_days = None
    for order in store.values():
        days = order.get("daysFromPurchase")
        if not isinstance(days, int):
            continue
        if newest_days is None or days < newest_days:
            newest_days = days
            newest = order
    if newest:
        state["newest_order_id"] = newest.get("orderId")
        state["newest_submitted"] = newest.get("localSubmittedDate")
    return state
