"""Build derived game/DLC catalog from raw/orders.json. Does not modify raw data."""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import defaultdict
from typing import Any

import store
import report
import catalog
import names


def abs_url(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("//"):
        return "https:" + url
    return url


def payment_text(order: dict[str, Any]) -> str:
    parts = []
    if order.get("localPaymentInstrumentNames"):
        parts.append(str(order["localPaymentInstrumentNames"]))
    for pi in order.get("paymentInstruments") or []:
        name = pi.get("localName") or ""
        if name:
            parts.append(str(name))
    return " | ".join(parts)


def parse_amount(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("\xa0", " ").strip()
    nums = re.findall(r"[0-9]+(?:[.,][0-9]+)?", text.replace(" ", ""))
    if not nums:
        return None
    raw = nums[-1]
    if raw.count(",") == 1 and raw.count(".") == 0:
        raw = raw.replace(",", ".")
    else:
        raw = raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def contains_any(text: str, needles: list[str]) -> bool:
    return any(n and n in text for n in needles)


def refund_reasons(order: dict[str, Any], rules: dict[str, Any]) -> list[str]:
    cfg = rules.get("refund") or {}
    states = set(cfg.get("item_states") or [])
    needles = cfg.get("local_status_contains") or []
    use_returned = cfg.get("use_is_returned", True)
    reasons = []
    for it in order.get("items") or []:
        st = it.get("itemState")
        loc = it.get("localStatus") or ""
        if st in states:
            reasons.append(f"itemState={st}")
        if contains_any(loc, needles):
            reasons.append(f"localStatus={loc}")
        if use_returned and it.get("isReturned") is True:
            reasons.append("isReturned")
    return sorted(set(reasons))


def is_redemption_gift(order: dict[str, Any], item: dict[str, Any], rules: dict[str, Any], amount: float | None) -> bool:
    cfg = rules.get("redemption_gift") or {}
    pay = payment_text(order)
    if not contains_any(pay, cfg.get("payment_contains") or []):
        return False
    if cfg.get("require_zero_amount") and (amount or 0) != 0:
        return False
    types = set(cfg.get("item_types") or [])
    states = set(cfg.get("item_states") or [])
    if types and item.get("itemTypeName") not in types:
        return False
    if states and item.get("itemState") not in states:
        return False
    return True


def is_promotional_zero(order: dict[str, Any], item: dict[str, Any], rules: dict[str, Any], amount: float | None) -> bool:
    cfg = rules.get("promotional_zero") or {}
    if not cfg.get("enabled"):
        return False
    if cfg.get("require_zero_amount") and (amount or 0) != 0:
        return False
    if cfg.get("empty_payment") and payment_text(order).strip():
        return False
    types = set(cfg.get("item_types") or [])
    return not types or item.get("itemTypeName") in types


def related_children(item: dict[str, Any]) -> list[dict[str, Any]]:
    rel = item.get("relatedItems") or {}
    out = []
    for ch in rel.get("items") or []:
        url = ch.get("productLink")
        sku = catalog.extract_sku(url)
        logo = (ch.get("logo") or {}).get("link")
        out.append(
            {
                "name": ch.get("title"),
                "image": abs_url(logo),
                "url": url,
                "sku": sku,
            }
        )
    return out


def collect_skus(orders: dict[str, dict[str, Any]]) -> set[str]:
    skus: set[str] = set()
    for order in orders.values():
        for item in order.get("items") or []:
            sku = catalog.extract_sku(item.get("productId")) or catalog.extract_sku(item.get("productOrParentLink"))
            if sku:
                skus.add(sku)
            for ch in related_children(item):
                if ch.get("sku"):
                    skus.add(ch["sku"])
    return skus


def parent_exclude_reasons(
    order: dict[str, Any],
    item: dict[str, Any],
    rules: dict[str, Any],
    amount: float | None,
    refunded: bool,
) -> list[str]:
    reasons: list[str] = []
    item_type = item.get("itemTypeName") or "Unknown"
    if refunded:
        reasons.append("refunded_order")
    if item_type in set(rules.get("exclude_item_types") or []):
        reasons.append(f"type={item_type}")
    if rules.get("consumable_always_exclude") and item_type == "Consumable":
        reasons.append("consumable")
    if is_redemption_gift(order, item, rules, amount):
        reasons.append("redemption_gift")
    if is_promotional_zero(order, item, rules, amount):
        reasons.append("promotional_zero")
    return reasons


def role_from_type_and_catalog(
    item_type: str | None,
    sku: str | None,
    title: str | None,
    cat: dict[str, Any],
) -> str:
    info = cat.get(sku) if sku else None
    if info and not info.get("missing") and info.get("product_type"):
        mapped = catalog.product_role(info, title)
        if mapped != "other":
            return mapped
    if item_type == "Durable":
        return "dlc"
    if item_type == "Game":
        return "game"
    return catalog.product_role(info, title)


def make_row(
    order: dict[str, Any],
    *,
    oid: str,
    amount: float | None,
    currency: str | None,
    idx: int,
    name: str | None,
    image: str | None,
    url: str | None,
    sku: str | None,
    item_type: str | None,
    item_state: str | None,
    local_status: str | None,
    role: str,
    from_bundle: str | None,
    catalog_type: str | None,
) -> dict[str, Any]:
    return {
        "order_id": oid,
        "vanity_order_id": order.get("vanityOrderId"),
        "submitted": order.get("localSubmittedDate"),
        "market": order.get("market"),
        "currency": currency,
        "order_amount": amount,
        "payment": payment_text(order),
        "item_index": idx,
        "item_type": item_type,
        "catalog_type": catalog_type,
        "item_state": item_state,
        "local_status": local_status,
        "name": name,
        "image": image,
        "store_url": url,
        "product_id": sku,
        "from_bundle": from_bundle,
        "role": role,
    }


def expand_item(
    order: dict[str, Any],
    item: dict[str, Any],
    *,
    oid: str,
    amount: float | None,
    currency: str | None,
    idx: int,
    cat: dict[str, Any],
) -> list[dict[str, Any]]:
    parent_title = item.get("localTitle")
    parent_sku = catalog.extract_sku(item.get("productId")) or catalog.extract_sku(item.get("productOrParentLink"))
    parent_type = item.get("itemTypeName")
    children = related_children(item)
    parent_info = cat.get(parent_sku) if parent_sku else None
    parent_cat_type = (parent_info or {}).get("product_type")

    if not children:
        role = role_from_type_and_catalog(parent_type, parent_sku, parent_title, cat)
        if role not in {"game", "dlc"}:
            return []
        return [
            make_row(
                order,
                oid=oid,
                amount=amount,
                currency=currency,
                idx=idx,
                name=parent_title,
                image=abs_url(item.get("logoLink")) or (parent_info or {}).get("image"),
                url=item.get("productOrParentLink"),
                sku=parent_sku,
                item_type=parent_type,
                item_state=item.get("itemState"),
                local_status=item.get("localStatus"),
                role=role,
                from_bundle=None,
                catalog_type=parent_cat_type,
            )
        ]

    rows: list[dict[str, Any]] = []
    child_roles: list[str] = []
    for child in children:
        sku = child.get("sku")
        info = cat.get(sku) if sku else None
        role = catalog.product_role(info, child.get("name"), parent_title)
        child_roles.append(role)
        if role not in {"game", "dlc"}:
            continue
        rows.append(
            make_row(
                order,
                oid=oid,
                amount=amount,
                currency=currency,
                idx=idx,
                name=(info or {}).get("title") or child.get("name"),
                image=child.get("image") or (info or {}).get("image"),
                url=child.get("url"),
                sku=sku,
                item_type=parent_type,
                item_state=item.get("itemState"),
                local_status=item.get("localStatus"),
                role=role,
                from_bundle=parent_title,
                catalog_type=(info or {}).get("product_type"),
            )
        )

    # 子项全是 DLC、父级本身是游戏时，父级仍是本体
    if "game" not in child_roles:
        parent_role = role_from_type_and_catalog(parent_type, parent_sku, parent_title, cat)
        if parent_role == "game":
            rows.insert(
                0,
                make_row(
                    order,
                    oid=oid,
                    amount=amount,
                    currency=currency,
                    idx=idx,
                    name=parent_title,
                    image=abs_url(item.get("logoLink")) or (parent_info or {}).get("image"),
                    url=item.get("productOrParentLink"),
                    sku=parent_sku,
                    item_type=parent_type,
                    item_state=item.get("itemState"),
                    local_status=item.get("localStatus"),
                    role="game",
                    from_bundle=parent_title,
                    catalog_type=parent_cat_type,
                ),
            )
    return rows


def unique_key(row: dict[str, Any]) -> str:
    if row.get("product_id"):
        return "sku:" + str(row["product_id"]).upper()
    name = (row.get("name") or "").strip().lower()
    return "name:" + name


def merge_unique(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = unique_key(row)
        if key == "name:":
            key = f"anon:{row.get('order_id')}:{row.get('item_index')}:{row.get('name')}"
        prev = merged.get(key)
        if prev is None:
            item = dict(row)
            item["order_ids"] = [row["order_id"]] if row.get("order_id") else []
            item["from_bundles"] = [row["from_bundle"]] if row.get("from_bundle") else []
            merged[key] = item
            continue
        if row.get("order_id") and row["order_id"] not in prev["order_ids"]:
            prev["order_ids"].append(row["order_id"])
        if row.get("from_bundle") and row["from_bundle"] not in prev["from_bundles"]:
            prev["from_bundles"].append(row["from_bundle"])
        if not prev.get("image") and row.get("image"):
            prev["image"] = row["image"]
        if not prev.get("store_url") and row.get("store_url"):
            prev["store_url"] = row["store_url"]
        if (row.get("submitted") or "") > (prev.get("submitted") or ""):
            prev["submitted"] = row.get("submitted")
    return list(merged.values())


def process() -> None:
    store.ensure_dirs()
    rules = store.load_json(store.RULES_PATH, {})
    orders = store.load_orders()
    cat = catalog.ensure_catalog(collect_skus(orders))

    occurrences: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    spend = defaultdict(lambda: {"amount": 0.0, "orders": 0})
    seen_spend_orders: set[str] = set()

    for oid, order in orders.items():
        amount = order.get("localTotalInDecimal")
        if amount is None:
            amount = parse_amount(order.get("localTotal"))
        currency = (order.get("currencyInfo") or {}).get("currencyCode")
        refund = refund_reasons(order, rules)
        included_this_order = False
        for idx, item in enumerate(order.get("items") or []):
            reasons = parent_exclude_reasons(order, item, rules, amount, bool(refund))
            if refund:
                reasons = sorted(set(reasons + ["refunded_order"] + [f"order:{r}" for r in refund]))
            if reasons:
                skipped.append(
                    {
                        "order_id": oid,
                        "name": item.get("localTitle"),
                        "item_type": item.get("itemTypeName"),
                        "item_state": item.get("itemState"),
                        "amount": amount,
                        "currency": currency,
                        "payment": payment_text(order),
                        "exclude_reasons": sorted(set(reasons)),
                    }
                )
                continue
            rows = expand_item(
                order, item, oid=oid, amount=amount, currency=currency, idx=idx, cat=cat
            )
            if not rows:
                skipped.append(
                    {
                        "order_id": oid,
                        "name": item.get("localTitle"),
                        "item_type": item.get("itemTypeName"),
                        "item_state": item.get("itemState"),
                        "amount": amount,
                        "currency": currency,
                        "payment": payment_text(order),
                        "exclude_reasons": ["not_game_or_dlc"],
                    }
                )
                continue
            occurrences.extend(rows)
            included_this_order = True
        if included_this_order and oid not in seen_spend_orders and amount:
            spend[currency or "?"]["amount"] += float(amount)
            spend[currency or "?"]["orders"] += 1
            seen_spend_orders.add(oid)

    library = merge_unique(occurrences)
    name_cache = names.ensure_names(library, cat)
    names.apply_names(library, name_cache)
    names.apply_names(occurrences, name_cache)
    library.sort(key=lambda r: (r.get("role") or "", r.get("display_name") or r.get("name") or ""))

    summary = {
        "raw_orders": len(orders),
        "library_items": len(library),
        "occurrence_items": len(occurrences),
        "skipped_items": len(skipped),
        "spend_by_currency": {k: {"amount": round(v["amount"], 2), "orders": v["orders"]} for k, v in spend.items()},
        "by_role": {},
        "catalog_entries": len(cat),
    }
    roles: dict[str, int] = defaultdict(int)
    for rec in library:
        roles[rec["role"]] += 1
    summary["by_role"] = dict(roles)

    store.save_json(store.DERIVED_DIR / "library.json", library)
    store.save_json(store.DERIVED_DIR / "occurrences.json", occurrences)
    store.save_json(store.DERIVED_DIR / "skipped.json", skipped)
    store.save_json(store.DERIVED_DIR / "summary.json", summary)

    csv_path = store.DERIVED_DIR / "library.csv"
    fieldnames = [
        "role",
        "name",
        "zh_name",
        "zh_source",
        "product_id",
        "catalog_type",
        "from_bundles",
        "submitted",
        "store_url",
        "image",
        "order_count",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for rec in library:
            w.writerow(
                {
                    "role": rec.get("role"),
                    "name": rec.get("name"),
                    "zh_name": rec.get("zh_name"),
                    "zh_source": rec.get("zh_source"),
                    "product_id": rec.get("product_id"),
                    "catalog_type": rec.get("catalog_type"),
                    "from_bundles": " | ".join(rec.get("from_bundles") or []),
                    "submitted": rec.get("submitted"),
                    "store_url": rec.get("store_url"),
                    "image": rec.get("image"),
                    "order_count": len(rec.get("order_ids") or []),
                }
            )

    report.write_result_html(summary, library, skipped)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"raw -> {store.ORDERS_PATH}")
    print(f"library -> {store.DERIVED_DIR / 'library.json'}")
    print(f"csv -> {csv_path}")
    print(f"html -> {store.ROOT / 'result.html'}")
    print("改筛选只编辑 rules.json 后重新运行 process_orders.py，不必重新抓取。")


if __name__ == "__main__":
    sys.exit(process() or 0)
