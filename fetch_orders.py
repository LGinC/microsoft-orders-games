"""Fetch Microsoft account order-list JSON and merge into raw/ by orderId.

Modes:
  incremental     Logged-in page `fetch()` from newest until overlap with stock
  full            Same, but continue until continuationToken is empty
  import-capture  Import bodies already captured by a `bsk` debug session

Pages are loaded with in-page fetch() so large responses are not truncated by
debug capture (8 MiB / body_limit). Existing raw/orders.json is never deleted;
new pages upsert by orderId.

Requires: bsk on PATH or %USERPROFILE%\\.local\\bin\\bsk.exe, extension connected.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import store

ORDERS_URL = (
    "https://account.microsoft.com/billing/orders"
    "?lang=zh-CN&period=SevenYears&type=All"
)
QUIET_URL = "https://account.microsoft.com/billing"
LIST_URL_SUBSTR = "billing/orders/list"
STALE_HINTS = (
    "latest cooperate sync",
    "observed content changed",
    "synchronize and replan",
    "cooperation_sync_required",
)
BATCH_PAGES = 4


def bsk_bin() -> str:
    env = os.environ.get("BSK_BIN")
    if env:
        return env
    local = Path.home() / ".local" / "bin" / ("bsk.exe" if os.name == "nt" else "bsk")
    if local.exists():
        return str(local)
    return "bsk"


def run_bsk(
    *args: str,
    check: bool = True,
    timeout: int | None = 120,
) -> dict[str, Any] | list[Any] | None:
    env = os.environ.copy()
    env["BSK_AUTO_START"] = "0"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    cmd = [bsk_bin(), *args]
    # bsk prints UTF-8 JSON; Windows default text mode is GBK and will crash on 中文.
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
    )
    text = (proc.stdout or "").strip()
    data = None
    if text.startswith("{") or text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
    if check and proc.returncode != 0:
        err = (proc.stderr or text or proc.stdout)[-2000:]
        raise RuntimeError(f"bsk {' '.join(args[:6])} failed ({proc.returncode}): {err}")
    return data


def get_list_body(session: str, req_id: str, chars: int) -> str | None:
    parts: list[str] = []
    offset = 0
    while offset < chars:
        data = run_bsk(
            "debug",
            "request",
            req_id,
            "--session",
            session,
            "--part",
            "response",
            "--offset",
            str(offset),
            "--max-chars",
            "4096",
            "--quiet",
            "--json",
        )
        if not isinstance(data, dict):
            return None
        rb = ((data.get("request") or {}).get("response_body") or {})
        text = rb.get("text") or ""
        nxt = rb.get("next_offset")
        parts.append(text)
        if nxt is None or nxt <= offset:
            break
        offset = nxt
    body = "".join(parts)
    if chars and len(body) >= chars:
        return body[:chars]
    return body or None


def parse_page(body: str) -> dict[str, Any] | None:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "orders" not in data:
        return None
    return data


def list_capture_requests(session: str) -> list[dict[str, Any]]:
    data = run_bsk(
        "debug",
        "requests",
        "--session",
        session,
        "--kind",
        "business",
        "--url",
        LIST_URL_SUBSTR,
        "--limit",
        "100",
        "--budget",
        "262144",
        "--json",
        check=False,
    )
    if not isinstance(data, dict):
        return []
    return list(data.get("requests") or [])


def ingest_page(
    store_orders: dict[str, dict[str, Any]],
    meta: dict[str, Any],
    page: dict[str, Any],
    *,
    source: str,
    known_before: set[str],
) -> dict[str, Any]:
    orders = list(page.get("orders") or [])
    ids = [o.get("orderId") for o in orders if o.get("orderId")]
    new_n, upd_n = store.upsert_orders(store_orders, orders, source=source, meta=meta)
    overlap = bool(ids) and all(i in known_before for i in ids)
    token = page.get("continuationToken") or None
    store.append_fetch_log(
        {
            "at": store.utc_now(),
            "source": source,
            "n": len(orders),
            "new": new_n,
            "updated": upd_n,
            "overlap": overlap,
            "token": token,
        }
    )
    return {
        "n": len(orders),
        "ids": ids,
        "new": new_n,
        "updated": upd_n,
        "overlap": overlap,
        "token": token,
        "empty": len(orders) == 0,
    }


def import_capture(session: str | None) -> None:
    if not session:
        status = run_bsk("status", "--json")
        sessions = (status or {}).get("sessions") or []
        if not sessions:
            raise SystemExit("没有活动的 bsk session。先打开订单页或运行 incremental。")
        session = sessions[0]["session_id"]
    print(f"从 debug 抓包导入 session={session}")
    reqs = list_capture_requests(session)
    store.ensure_dirs()
    store_orders = store.load_orders()
    meta = store.load_meta()
    state = store.load_state()
    known = set(store_orders)
    imported = 0
    truncated = 0
    new_total = 0
    upd_total = 0
    for req in reqs:
        rb = req.get("response_body") or {}
        if rb.get("state") == "truncated":
            truncated += 1
            continue
        chars = rb.get("chars") or 0
        if rb.get("state") != "available" or chars <= 2:
            continue
        body = get_list_body(session, req["id"], chars)
        if not body:
            continue
        page = parse_page(body)
        if not page:
            continue
        stats = ingest_page(
            store_orders, meta, page, source=f"capture:{req['id']}", known_before=known
        )
        imported += 1
        new_total += stats["new"]
        upd_total += stats["updated"]
        known.update(stats["ids"])
    store.save_orders(store_orders)
    store.save_meta(meta)
    state = store.refresh_state(
        state,
        store_orders,
        last_fetch_at=store.utc_now(),
        last_fetch_mode="import-capture",
        truncated_pages=truncated,
        complete=False,
    )
    store.save_state(state)
    print(
        f"导入 {imported} 页，新增 {new_total}，更新 {upd_total}，"
        f"截断页 {truncated}，库存 {len(store_orders)}"
    )


def extract_page(data: Any) -> dict[str, Any] | None:
    if isinstance(data, dict):
        if "orders" in data:
            return data
        for key in ("result", "value", "data", "output"):
            found = extract_page(data.get(key))
            if found:
                return found
    return None


def unwrap_eval(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    for key in ("result", "value", "data", "output"):
        if key in data and data[key] is not None:
            inner = data[key]
            if isinstance(inner, dict) and any(k in inner for k in ("ok", "status", "orders", "error")):
                return inner
            if key == "result" and isinstance(inner, dict):
                nested = unwrap_eval(inner)
                if nested is not inner:
                    return nested
    return data


def cooperate_sync(session: str, tab_id: int | None, *, wait_first: bool = False) -> str:
    if wait_first:
        args = ["cooperate", "wait", "--session", session, "--json"]
        if tab_id is not None:
            args.extend(["--tab-id", str(tab_id)])
        run_bsk(*args, check=False, timeout=60)
    sync_args = ["cooperate", "sync", "--session", session, "--json"]
    if tab_id is not None:
        sync_args.extend(["--tab-id", str(tab_id)])
    last_err = None
    for _ in range(4):
        try:
            data = run_bsk(*sync_args, check=False, timeout=90)
        except Exception as exc:
            last_err = exc
            time.sleep(1)
            continue
        if isinstance(data, dict) and data.get("context_id"):
            return str(data["context_id"])
        last_err = data
        time.sleep(1)
    raise SystemExit(f"无法 cooperate sync（需要页面已登录订单历史）。详情: {last_err}")


def cooperate_context(session: str, tab_id: int | None) -> str:
    return cooperate_sync(session, tab_id, wait_first=True)


def is_stale_context(message: str | None) -> bool:
    text = (message or "").lower()
    return any(h.lower() in text for h in STALE_HINTS)


def extract_csrf(obj: Any) -> str:
    if isinstance(obj, dict):
        csrf = obj.get("csrf")
        if csrf:
            return str(csrf)
        for val in obj.values():
            found = extract_csrf(val)
            if found:
                return found
    return ""


def bsk_error_message(data: Any) -> str:
    if isinstance(data, dict):
        return str(data.get("message") or data.get("code") or "")
    return str(data or "")


def should_stop(mode: str, known_at_start: set[str], stats: dict[str, Any]) -> str | None:
    if mode == "incremental" and known_at_start and stats.get("overlap") and (stats.get("n") or 0) > 0:
        return "incremental"
    if not stats.get("token"):
        return "complete"
    return None


def run_bsk_with_context(
    session: str,
    tab_id: int | None,
    *args: str,
    wait_first: bool = False,
) -> dict[str, Any] | list[Any] | None:
    last: Any = None
    for attempt in range(5):
        ctx = cooperate_sync(session, tab_id, wait_first=wait_first and attempt == 0)
        rest = list(args[1:])
        positional: list[str] = []
        flags: list[str] = []
        for item in rest:
            if item.startswith("-") or flags:
                flags.append(item)
            else:
                positional.append(item)
        cmd = [args[0], *positional, "--session", session, "--context", ctx, *flags]
        if tab_id is not None:
            cmd = [
                args[0],
                *positional,
                "--session",
                session,
                "--tab-id",
                str(tab_id),
                "--context",
                ctx,
                *flags,
            ]
        data = run_bsk(*cmd, check=False)
        last = data
        msg = bsk_error_message(data)
        if isinstance(data, dict) and data.get("code") and is_stale_context(msg):
            continue
        if isinstance(data, dict) and data.get("code") == "permission_denied":
            continue
        if isinstance(data, dict) and not data.get("code"):
            return data
        if isinstance(data, dict) and data.get("ok"):
            return data
    raise RuntimeError(f"bsk {' '.join(args[:4])} 失败: {last}")


CSRF_JS = r"""
(() => {
  const input = document.querySelector('input[name="__RequestVerificationToken"]');
  if (input && input.value) return { csrf: input.value, href: location.href };
  const meta = document.querySelector('meta[name="__RequestVerificationToken"], meta[name="request-verification-token"]');
  if (meta && meta.content) return { csrf: meta.content, href: location.href };
  const byId = document.getElementById('__RequestVerificationToken');
  if (byId && byId.value) return { csrf: byId.value, href: location.href };
  const cookie = document.cookie.split(';').map((s) => s.trim()).find((s) =>
    s.toLowerCase().startsWith('__requestverificationtoken='));
  return {
    csrf: cookie ? decodeURIComponent(cookie.split('=').slice(1).join('=')) : '',
    href: location.href,
  };
})()
"""

FETCH_BATCH_JS = r"""
(async () => {
  const continuation = __CONTINUATION__;
  const givenCsrf = __CSRF__;
  const maxPages = __MAX__;
  const csrf = givenCsrf || (() => {
    const input = document.querySelector('input[name="__RequestVerificationToken"]');
    if (input && input.value) return input.value;
    return '';
  })();
  async function one(token) {
    const params = new URLSearchParams({
      period: 'SevenYears',
      orderTypeFilter: 'All',
      filterChangeCount: '0',
      isInD365Orders: 'true',
      isPiDetailsRequired: 'true',
      timeZoneOffsetMinutes: String(new Date().getTimezoneOffset()),
    });
    if (token) params.set('continuationToken', token);
    const headers = {
      Accept: 'application/json, text/plain, */*',
      'X-Requested-With': 'XMLHttpRequest',
    };
    if (csrf) headers.__requestverificationtoken = csrf;
    const res = await fetch('/billing/orders/list?' + params.toString(), {
      method: 'GET',
      credentials: 'include',
      headers,
    });
    const text = await res.text();
    let body = null;
    try { body = JSON.parse(text); } catch (e) {
      return { ok: false, status: res.status, error: 'not_json', preview: text.slice(0, 240) };
    }
    if (!res.ok) {
      return { ok: false, status: res.status, error: 'http', preview: text.slice(0, 240) };
    }
    return {
      ok: true,
      status: res.status,
      continuationToken: body && body.continuationToken ? body.continuationToken : null,
      filterChangeCount: body && body.filterChangeCount,
      orders: (body && body.orders) || [],
    };
  }
  const pages = [];
  let token = continuation;
  for (let i = 0; i < maxPages; i++) {
    const page = await one(token);
    if (!page.ok) {
      return { ok: false, status: page.status, error: page.error, preview: page.preview, pages };
    }
    pages.push(page);
    token = page.continuationToken;
    if (!token) break;
  }
  return { ok: true, pages, continuationToken: token || null };
})()
"""


def eval_js(
    session: str,
    tab_id: int | None,
    context_id: str,
    js: str,
    timeout: str = "90s",
) -> dict[str, Any]:
    args = [
        "evaluate",
        "--session",
        session,
        "--context",
        context_id,
        "--timeout",
        timeout,
        "--json",
        js,
    ]
    if tab_id is not None:
        args = [
            "evaluate",
            "--session",
            session,
            "--tab-id",
            str(tab_id),
            "--context",
            context_id,
            "--timeout",
            timeout,
            "--json",
            js,
        ]
    data = run_bsk(*args, check=False, timeout=max(30, int(timeout.rstrip("sm")) + 20) if timeout.endswith("s") else 200)
    if not isinstance(data, dict):
        raise RuntimeError(f"evaluate 无 JSON 输出: {data}")
    if data.get("code") and data.get("message"):
        raise RuntimeError(str(data.get("message")))
    payload = unwrap_eval(data)
    if isinstance(payload, dict):
        return payload
    return {"value": payload}


def eval_with_fresh_context(
    session: str,
    tab_id: int | None,
    js: str,
    *,
    timeout: str = "180s",
    wait_first: bool = False,
) -> dict[str, Any]:
    last_err: Exception | None = None
    for attempt in range(6):
        context_id = cooperate_sync(session, tab_id, wait_first=wait_first and attempt == 0)
        try:
            return eval_js(session, tab_id, context_id, js, timeout=timeout)
        except RuntimeError as exc:
            last_err = exc
            if is_stale_context(str(exc)):
                continue
            raise
    raise RuntimeError(f"evaluate 多次因页面变化失败: {last_err}")


def eval_fetch_batch(
    session: str,
    tab_id: int | None,
    continuation: str | None,
    csrf: str,
    max_pages: int = BATCH_PAGES,
) -> dict[str, Any]:
    js = (
        FETCH_BATCH_JS.replace("__CONTINUATION__", json.dumps(continuation, ensure_ascii=False))
        .replace("__CSRF__", json.dumps(csrf, ensure_ascii=False))
        .replace("__MAX__", str(int(max_pages)))
    )
    payload = eval_with_fresh_context(session, tab_id, js, timeout="180s", wait_first=False)
    if payload.get("pages") is None and "orders" in payload:
        payload = {"ok": True, "pages": [payload], "continuationToken": payload.get("continuationToken")}
    return payload


def wait_session_browser() -> str:
    for _ in range(30):
        status = run_bsk("status", "--json", check=False) or {}
        browsers = status.get("browsers") or []
        if browsers:
            return browsers[0].get("instance_id") or browsers[0].get("label")
        time.sleep(1)
    raise SystemExit("浏览器扩展未连接。打开 Chrome/Edge/Brave 并启用 BrowserSkill 本地连接。")


def capture_loop(mode: str) -> None:
    store.ensure_dirs()
    store_orders = store.load_orders()
    meta = store.load_meta()
    state = store.load_state()
    known_at_start = set(store_orders)
    browser = wait_session_browser()
    started = run_bsk("session", "start", "--browser", browser, "--name", "orders-fetch", "--json")
    session = started["session_id"]
    last_token = None
    print(f"session {session} browser {browser} mode={mode} via=page-fetch")
    try:
        nav = run_bsk("navigate", ORDERS_URL, "--session", session, "--json")
        tab_id = (nav or {}).get("tab_id")
        print("等待订单页登录态…")
        time.sleep(4)
        csrf_info = eval_with_fresh_context(session, tab_id, CSRF_JS, timeout="30s", wait_first=True)
        csrf = extract_csrf(csrf_info)
        print(f"CSRF={'已拿到' if csrf else '空，将靠 cookie'}")
        print("转到静止账单页，避免订单列表自动翻页打乱 context…")
        nav2 = run_bsk_with_context(
            session,
            tab_id,
            "navigate",
            QUIET_URL,
            "--json",
            wait_first=False,
        )
        tab_id = (nav2 or {}).get("tab_id") or tab_id
        time.sleep(2)
        page_n = 0
        stop = False
        while not stop:
            payload = eval_fetch_batch(session, tab_id, last_token, csrf, max_pages=BATCH_PAGES)
            if not payload.get("ok"):
                print(
                    f"  fetch 失败 status={payload.get('status')} "
                    f"error={payload.get('error')} preview={payload.get('preview')}"
                )
                if payload.get("status") in (401, 403):
                    print("登录态失效，请在扩展已连接的浏览器里保持微软账户登录后重试。")
                    break
                raise SystemExit(f"连续 fetch 失败: {payload}")
            pages = list(payload.get("pages") or [])
            if not pages:
                print("  本批没有页，结束。")
                break
            for page in pages:
                page_n += 1
                stats = ingest_page(
                    store_orders,
                    meta,
                    {
                        "continuationToken": page.get("continuationToken"),
                        "filterChangeCount": page.get("filterChangeCount"),
                        "orders": page.get("orders") or [],
                    },
                    source=f"fetch:{page_n}",
                    known_before=known_at_start,
                )
                last_token = stats["token"]
                print(
                    f"  页 {page_n} n={stats['n']} new={stats['new']} "
                    f"upd={stats['updated']} overlap={stats['overlap']} token={bool(last_token)}"
                )
                store.save_orders(store_orders)
                store.save_meta(meta)
                why = should_stop(mode, known_at_start, stats)
                if why == "incremental":
                    print("增量：本页订单均已在库存中，停止。")
                    stop = True
                    break
                if why == "complete":
                    print("continuationToken 为空，结束。")
                    state["complete"] = True
                    stop = True
                    break
    finally:
        try:
            run_bsk("session", "stop", session, check=False)
        except Exception:
            pass
    state = store.refresh_state(
        state,
        store_orders,
        last_fetch_at=store.utc_now(),
        last_fetch_mode=mode,
        truncated_pages=0,
        resume_continuation_token=last_token if not state.get("complete") else None,
    )
    if mode == "full" and state.get("complete"):
        state["last_full_at"] = store.utc_now()
    store.save_orders(store_orders)
    store.save_meta(meta)
    store.save_state(state)
    added = len(set(store_orders) - known_at_start)
    print(f"完成 mode={mode} 新增 {added} 库存 {len(store_orders)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="增量抓取微软订单原始 JSON")
    parser.add_argument(
        "mode",
        nargs="?",
        default="incremental",
        choices=["incremental", "full", "import-capture"],
        help="incremental=页面 fetch 从最新页抓到与库存重叠; full=一直抓到 token 空; import-capture=导入 debug 抓包",
    )
    parser.add_argument("--session", default=None, help="import-capture 使用的 session id")
    args = parser.parse_args()
    if args.mode == "import-capture":
        import_capture(args.session)
    else:
        capture_loop(args.mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
