"""Lookup Microsoft Store products by SKU (Store ID / bigId).

Uses the public Display Catalog API. Results are cached in raw/catalog.json
so later incremental runs only request unknown IDs.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable

import store

CATALOG_PATH = store.RAW_DIR / "catalog.json"
CATALOG_URL = "https://displaycatalog.mp.microsoft.com/v7.0/products"
BATCH = 12
SKU_RE = re.compile(r"/store/p/[^/?#]+/([A-Za-z0-9]{10,15})(?:/\d{4})?", re.I)
BARE_SKU_RE = re.compile(r"^[A-Za-z0-9]{10,15}$")

DLC_TITLE_HINTS = (
    "dlc",
    "expansion",
    "season pass",
    "seasonpass",
    "content pack",
    "supporter pack",
    "bonus",
    "soundtrack",
    "skin",
    "costume",
    "bgm",
    "ekstra",
    "içerik",
    "extra content",
    "扩展",
    "资料片",
    "季票",
    "通行证",
    "豪华包",
    "附加内容",
    "特典",
    "组合包",
    "礼包",
)
BUNDLE_IS_DLC_HINTS = (
    "expansion pass",
    "season pass",
    "dlc",
    "组合包",
    "季票",
    "资料片",
)


def extract_sku(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if BARE_SKU_RE.match(text):
        return text.upper()
    m = SKU_RE.search(text)
    if m:
        return m.group(1).upper()
    return None


def load_catalog() -> dict[str, Any]:
    return store.load_json(CATALOG_PATH, {})


def save_catalog(catalog: dict[str, Any]) -> None:
    store.save_json(CATALOG_PATH, catalog)


def _pick_image(product: dict[str, Any]) -> str | None:
    lp = (product.get("LocalizedProperties") or [{}])[0]
    for img in lp.get("Images") or []:
        if (img.get("ImagePurpose") or "").lower() in {"poster", "boxart", "logo", "tile"}:
            uri = img.get("Uri")
            if uri:
                return uri if uri.startswith("http") else "https:" + uri.lstrip("/")
    for img in lp.get("Images") or []:
        uri = img.get("Uri")
        if uri:
            return uri if uri.startswith("http") else "https:" + uri.lstrip("/")
    return None


def slim_product(product: dict[str, Any]) -> dict[str, Any]:
    lp = (product.get("LocalizedProperties") or [{}])[0]
    props = product.get("Properties") or {}
    return {
        "product_id": (product.get("ProductId") or "").upper(),
        "product_type": product.get("ProductType"),
        "title": lp.get("ProductTitle") or lp.get("ShortTitle"),
        "has_add_ons": props.get("HasAddOns"),
        "category": props.get("Category") or props.get("PrimaryCategory"),
        "image": _pick_image(product),
        "missing": False,
    }


def _fetch_batch(ids: list[str], market: str) -> list[dict[str, Any]]:
    params = {
        "bigIds": ",".join(ids),
        "market": market,
        "languages": "zh-CN,en-US",
        "actionFilter": "Browse",
    }
    url = CATALOG_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 orders-catalog"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        data = json.load(resp)
    return list(data.get("Products") or [])


def ensure_catalog(skus: Iterable[str], markets: tuple[str, ...] = ("US", "TR", "SA")) -> dict[str, Any]:
    catalog = load_catalog()
    wanted = sorted({s.upper() for s in skus if s})
    missing = [s for s in wanted if s not in catalog]
    if not missing:
        return catalog
    print(f"目录补全 {len(missing)} 个 SKU（库存已有 {len(catalog)}）")
    for i in range(0, len(missing), BATCH):
        chunk = missing[i : i + BATCH]
        found: dict[str, dict[str, Any]] = {}
        for market in markets:
            leftover = [s for s in chunk if s not in found]
            if not leftover:
                break
            try:
                products = _fetch_batch(leftover, market)
            except urllib.error.HTTPError as exc:
                print(f"  批次失败 market={market} {exc.code}")
                time.sleep(0.4)
                continue
            except Exception as exc:
                print(f"  批次异常 market={market} {exc}")
                time.sleep(0.4)
                continue
            for product in products:
                slim = slim_product(product)
                pid = slim["product_id"]
                if pid:
                    found[pid] = slim
            time.sleep(0.15)
        for sku in chunk:
            catalog[sku] = found.get(sku) or {"product_id": sku, "missing": True}
        save_catalog(catalog)
        print(f"  {min(i + BATCH, len(missing))}/{len(missing)}")
    return catalog


def product_role(
    info: dict[str, Any] | None,
    title: str | None = None,
    bundle_title: str | None = None,
) -> str:
    """Return game, dlc, or other."""
    ptype = (info or {}).get("product_type") if info and not info.get("missing") else None
    if ptype == "Game":
        return "game"
    if ptype == "Durable":
        return "dlc"
    if ptype in {"Consumable", "Application", "Movie", "Pass"}:
        return "other"
    text = (title or "") + " " + ((info or {}).get("title") or "")
    low = text.lower()
    if any(h in low for h in DLC_TITLE_HINTS):
        return "dlc"
    bundle = (bundle_title or "").lower()
    if bundle and any(h in bundle for h in BUNDLE_IS_DLC_HINTS):
        return "dlc"
    return "game"
