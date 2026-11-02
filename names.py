"""Resolve Chinese titles for games/DLC.

Priority:
  1. Microsoft Store catalog ProductTitle if it already contains CJK (official listing)
  2. Steam store search / appdetails?l=schinese (official Steam CN listing)
  3. Bangumi name_cn (community / semi-official)

Cached in raw/names.json; later runs only look up missing keys.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from typing import Any

import store

NAMES_PATH = store.RAW_DIR / "names.json"
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
UA = "Mozilla/5.0 orders-names/1.0"


def has_cjk(text: str | None) -> bool:
    return bool(CJK_RE.search(text or ""))


def norm(text: str | None) -> str:
    s = (text or "").lower()
    s = re.sub(r"[™®©:_/\\\-\(\)\[\]\{\}'\".!,?|]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def similar(a: str | None, b: str | None) -> float:
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb or na in nb or nb in na:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def load_names() -> dict[str, Any]:
    return store.load_json(NAMES_PATH, {})


def save_names(data: dict[str, Any]) -> None:
    store.save_json(NAMES_PATH, data)


def _get_json(url: str, timeout: int = 25) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def steam_search(term: str) -> list[dict[str, Any]]:
    q = urllib.parse.urlencode({"term": term, "l": "schinese", "cc": "cn"})
    url = "https://store.steampowered.com/api/storesearch/?" + q
    data = _get_json(url)
    return list(data.get("items") or [])


def steam_details(appid: int) -> dict[str, Any] | None:
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&l=schinese"
    data = _get_json(url)
    block = data.get(str(appid)) or {}
    if not block.get("success"):
        return None
    return block.get("data") or None


def bangumi_search(term: str) -> list[dict[str, Any]]:
    url = "https://api.bgm.tv/search/subject/" + urllib.parse.quote(term) + "?type=4&responseGroup=small"
    data = _get_json(url)
    if isinstance(data, dict):
        return list(data.get("list") or [])
    return []


def _pick_steam(term: str) -> tuple[str, str] | None:
    items = steam_search(term)
    best = None
    best_score = 0.0
    for it in items[:6]:
        score = max(similar(term, it.get("name")), similar(term, str(it.get("id"))))
        if score > best_score:
            best_score = score
            best = it
    if not best or best_score < 0.45:
        return None
    name = best.get("name") or ""
    if has_cjk(name):
        return name, "steam"
    appid = best.get("id")
    if not appid:
        return None
    details = steam_details(int(appid))
    dname = (details or {}).get("name") or ""
    if has_cjk(dname):
        return dname, "steam"
    return None


def _pick_bangumi(term: str) -> tuple[str, str] | None:
    items = bangumi_search(term)
    best = None
    best_score = 0.0
    for it in items[:6]:
        score = max(similar(term, it.get("name")), similar(term, it.get("name_cn")))
        if score > best_score:
            best_score = score
            best = it
    if not best or best_score < 0.4:
        return None
    cn = (best.get("name_cn") or "").strip()
    if has_cjk(cn):
        return cn, "bangumi"
    return None


def lookup_zh(original: str) -> dict[str, Any]:
    original = (original or "").strip()
    if not original:
        return {"zh_name": None, "source": None, "original": original}
    if has_cjk(original):
        return {"zh_name": original, "source": "listing", "original": original}
    try:
        hit = _pick_steam(original)
        if hit:
            return {"zh_name": hit[0], "source": hit[1], "original": original}
    except Exception:
        pass
    time.sleep(0.2)
    try:
        hit = _pick_bangumi(original)
        if hit:
            return {"zh_name": hit[0], "source": hit[1], "original": original}
    except Exception:
        pass
    return {"zh_name": None, "source": None, "original": original}


def name_key(sku: str | None, original: str | None) -> str:
    if sku:
        return "sku:" + sku.upper()
    return "name:" + norm(original)


def ensure_names(rows: list[dict[str, Any]], catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    cache = load_names()
    catalog = catalog or {}
    todo: list[tuple[str, str]] = []
    for row in rows:
        sku = row.get("product_id")
        original = row.get("name") or ""
        key = name_key(sku, original)
        if key in cache:
            continue
        cat = catalog.get((sku or "").upper()) if sku else None
        cat_title = (cat or {}).get("title") or ""
        if has_cjk(original):
            cache[key] = {"zh_name": original, "source": "listing", "original": original}
            continue
        if has_cjk(cat_title):
            cache[key] = {"zh_name": cat_title, "source": "ms-catalog", "original": original}
            continue
        todo.append((key, original))
    if todo:
        print(f"译名补全 {len(todo)} 条（缓存已有 {len(cache)}）")
    for i, (key, original) in enumerate(todo, 1):
        cache[key] = lookup_zh(original)
        if i % 20 == 0 or i == len(todo):
            save_names(cache)
            print(f"  {i}/{len(todo)}")
        time.sleep(0.25)
    save_names(cache)
    return cache


def apply_names(rows: list[dict[str, Any]], cache: dict[str, Any]) -> None:
    for row in rows:
        key = name_key(row.get("product_id"), row.get("name"))
        info = cache.get(key) or {}
        zh = info.get("zh_name")
        row["zh_name"] = zh
        row["zh_source"] = info.get("source")
        row["display_name"] = zh or row.get("name")
