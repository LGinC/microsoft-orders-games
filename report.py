"""Write result.html from derived summary + library."""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

import store

DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")


def date_key(text: str | None) -> int:
    m = DATE_RE.search(text or "")
    if not m:
        return 0
    return int(m.group(1)) * 10000 + int(m.group(2)) * 100 + int(m.group(3))

ROLE_LABEL = {"game": "游戏本体", "dlc": "DLC"}
SKIP_LABEL = {
    "redemption_gift": "兑换赠品",
    "promotional_zero": "0 元活动/奖励",
    "consumable": "局内道具",
    "type=StoredValue": "账户余额",
    "type=CSV": "账户信用",
    "type=Software": "软件/订阅类",
    "type=Movie": "影片",
    "type=Unknown": "未知类型",
    "type=Subscription": "订阅",
    "refunded_order": "退款订单",
    "order:isReturned": "已退回",
    "order:itemState=GiftRefunded": "礼品已退回",
    "order:itemState=Refunded": "已退款",
}


def _payload(summary: dict[str, Any], library: list[dict[str, Any]], skipped: list[dict[str, Any]]) -> dict[str, Any]:
    state = store.load_state()
    skip_counts: Counter[str] = Counter()
    for row in skipped:
        for reason in row.get("exclude_reasons") or ["other"]:
            key = reason if not str(reason).startswith("order:localStatus") else (
                "order:itemState=Refunded" if "已退款" in str(reason) else "order:itemState=GiftRefunded"
            )
            skip_counts[key] += 1
    slim = []
    for rec in library:
        slim.append(
            {
                "name": rec.get("name"),
                "zh_name": rec.get("zh_name"),
                "zh_source": rec.get("zh_source"),
                "display_name": rec.get("display_name") or rec.get("zh_name") or rec.get("name"),
                "role": rec.get("role"),
                "submitted": rec.get("submitted"),
                "date_key": date_key(rec.get("submitted")),
                "currency": rec.get("currency"),
                "amount": rec.get("order_amount") or 0,
                "charged": rec.get("charged_amount") or 0,
                "charge_row": bool(rec.get("charge_this_row")),
                "image": rec.get("image"),
                "url": rec.get("store_url"),
                "product_id": rec.get("product_id"),
                "vanity": rec.get("vanity_order_id"),
                "state": rec.get("item_state"),
                "from_bundles": rec.get("from_bundles") or [],
                "catalog_type": rec.get("catalog_type"),
                "order_count": len(rec.get("order_ids") or []),
            }
        )
    spend = {}
    for code, info in (summary.get("spend_by_currency") or {}).items():
        spend[code] = {
            "amount": round(float(info.get("amount") or 0), 2),
            "orders": info.get("orders") or 0,
        }
    return {
        "generated_at": store.utc_now(),
        "raw_orders": summary.get("raw_orders"),
        "library_items": summary.get("library_items"),
        "skipped_items": summary.get("skipped_items"),
        "by_role": summary.get("by_role") or {},
        "spend": spend,
        "complete": bool(state.get("complete")),
        "truncated_pages": state.get("truncated_pages") or 0,
        "last_fetch_at": state.get("last_fetch_at"),
        "last_fetch_mode": state.get("last_fetch_mode"),
        "newest_submitted": state.get("newest_submitted"),
        "skip_counts": dict(skip_counts.most_common()),
        "library": slim,
    }


def write_result_html(summary: dict[str, Any], library: list[dict[str, Any]], skipped: list[dict[str, Any]]) -> None:
    data = _payload(summary, library, skipped)
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    html = HTML_TEMPLATE.replace("__DATA__", blob)
    path = store.ROOT / "result.html"
    path.write_text(html, encoding="utf-8")
    print(f"html -> {path}")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>微软商店订单分析 · 已购游戏与 DLC</title>
<style>
  :root {
    --bg: #0b0f14;
    --surface: #12171e;
    --elev: #181e27;
    --line: #2a3340;
    --text: #eef1f5;
    --muted: #8b95a7;
    --faint: #5c6778;
    --accent: #3d8bfd;
    --accent-2: #1a3a6b;
    --game: #5aa2ff;
    --dlc: #a78bfa;
    --warn: #e0b15a;
    --ok: #3dd68c;
    --shadow: 0 12px 40px rgba(0,0,0,.28);
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; }
  body {
    min-height: 100vh;
    color: var(--text);
    font: 14px/1.55 "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    background:
      radial-gradient(900px 420px at 8% -8%, #152238 0%, transparent 55%),
      radial-gradient(700px 380px at 100% 0%, #1a1630 0%, transparent 50%),
      var(--bg);
  }
  .top {
    position: sticky; top: 0; z-index: 20;
    backdrop-filter: blur(16px);
    background: rgba(11,15,20,.78);
    border-bottom: 1px solid var(--line);
  }
  .top-inner, .wrap {
    max-width: 1280px; margin: 0 auto; padding: 0 28px;
  }
  .top-inner {
    display: flex; align-items: center; gap: 16px;
    min-height: 64px;
  }
  .mark {
    width: 32px; height: 32px; border-radius: 9px;
    background: linear-gradient(135deg, #3d8bfd, #7c5cff);
    display: grid; place-items: center; font-weight: 800; color: #fff; font-size: 13px;
    flex: none;
  }
  .brand h1 { margin: 0; font-size: 16px; font-weight: 650; letter-spacing: .01em; }
  .brand p { margin: 0; color: var(--muted); font-size: 12px; }
  .stamp { margin-left: auto; color: var(--faint); font-size: 12px; text-align: right; }
  .wrap { padding-top: 24px; padding-bottom: 64px; }
  .kpis {
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 12px; margin-bottom: 18px;
  }
  .kpi {
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 16px 18px;
    box-shadow: var(--shadow);
  }
  .kpi .k { color: var(--muted); font-size: 12px; letter-spacing: .04em; text-transform: uppercase; }
  .kpi .v { font-size: 26px; font-weight: 680; margin-top: 6px; letter-spacing: -.02em; }
  .kpi.accent { border-color: #2c4f86; background: linear-gradient(180deg, #152238, var(--surface)); }
  .kpi.accent .v { color: var(--accent); }
  .notice {
    background: #231c12; border: 1px solid #5a4620; color: var(--warn);
    border-radius: 10px; padding: 10px 14px; margin-bottom: 18px; font-size: 13px;
  }
  .split {
    display: grid; grid-template-columns: 1.4fr .8fr; gap: 12px; margin-bottom: 22px;
  }
  .panel {
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 14px;
    overflow: hidden;
  }
  .panel h2 {
    margin: 0; padding: 14px 16px 0;
    font-size: 13px; font-weight: 650; color: var(--muted);
    letter-spacing: .06em; text-transform: uppercase;
  }
  .panel .hint { padding: 4px 16px 8px; color: var(--faint); font-size: 12px; }
  table.data { width: 100%; border-collapse: collapse; }
  table.data th, table.data td {
    padding: 9px 16px; text-align: left; border-top: 1px solid var(--line);
    font-variant-numeric: tabular-nums;
  }
  table.data th { color: var(--faint); font-size: 11px; font-weight: 650; letter-spacing: .04em; }
  table.data td.num { text-align: right; font-weight: 600; }
  .bar {
    height: 4px; border-radius: 99px; background: #243044; overflow: hidden; margin-top: 6px;
  }
  .bar > i { display: block; height: 100%; background: var(--accent); border-radius: 99px; }
  .lib-head {
    display: flex; align-items: flex-end; justify-content: space-between; gap: 12px;
    margin: 8px 0 12px;
  }
  .lib-head h2 { margin: 0; font-size: 18px; font-weight: 650; }
  .lib-head .sub { color: var(--muted); font-size: 13px; }
  .toolbar {
    position: sticky; top: 64px; z-index: 10;
    display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
    background: rgba(11,15,20,.86); backdrop-filter: blur(12px);
    border: 1px solid var(--line); border-radius: 12px;
    padding: 10px 12px; margin-bottom: 14px;
  }
  .chip, select, .toolbar input, .view-btn {
    border: 1px solid var(--line); background: var(--elev); color: var(--muted);
    border-radius: 8px; padding: 7px 11px; font: inherit; cursor: pointer;
  }
  .chip.on, .view-btn.on {
    color: #fff; background: var(--accent); border-color: var(--accent);
  }
  .toolbar input {
    flex: 1; min-width: 180px; cursor: text; color: var(--text);
  }
  .toolbar input:focus, select:focus { outline: 2px solid #3d8bfd55; border-color: var(--accent); }
  .count { color: var(--faint); font-size: 12px; margin-left: auto; }
  .views { display: flex; gap: 4px; }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 12px;
  }
  .item {
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 12px;
    display: grid;
    grid-template-columns: 76px 1fr;
    gap: 12px;
    transition: border-color .15s, transform .15s;
  }
  .item:hover { border-color: #3d5a86; transform: translateY(-1px); }
  .cover, .item img.cover, .ph {
    width: 76px; height: 76px; object-fit: cover; border-radius: 10px;
    background: var(--elev); display: block;
  }
  .item h3 { margin: 2px 0 4px; font-size: 14.5px; font-weight: 650; }
  .item h3 a { color: inherit; text-decoration: none; }
  .item h3 a:hover { color: var(--accent); }
  .meta { color: var(--muted); font-size: 12px; }
  .tag {
    display: inline-block; font-size: 10px; padding: 1px 7px;
    border-radius: 999px; margin-right: 6px; font-weight: 700; letter-spacing: .04em;
  }
  .tag.game { background: #173154; color: var(--game); }
  .tag.dlc { background: #2b2150; color: var(--dlc); }
  .price {
    margin-top: 6px; font-variant-numeric: tabular-nums; font-weight: 650; color: var(--text);
  }
  .kids { margin-top: 8px; grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 6px; }
  .kid {
    background: var(--elev); border-radius: 8px; padding: 3px 8px;
    font-size: 11px; color: var(--muted);
  }
  .empty { color: var(--muted); padding: 36px 0; text-align: center; }
  .tbl-wrap { overflow: auto; border: 1px solid var(--line); border-radius: 14px; background: var(--surface); }
  table.lib { width: 100%; border-collapse: collapse; min-width: 860px; }
  table.lib th, table.lib td {
    padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: middle;
    text-align: left;
  }
  table.lib th {
    position: sticky; top: 0; background: #161c25; color: var(--faint);
    font-size: 11px; letter-spacing: .05em; text-transform: uppercase; font-weight: 650;
  }
  table.lib th.sortable { cursor: pointer; user-select: none; }
  table.lib th.sortable:hover { color: var(--text); }
  table.lib td.num, table.lib th.num { text-align: right; font-variant-numeric: tabular-nums; }
  table.lib .row-title { display: flex; align-items: center; gap: 10px; }
  table.lib .row-title img, table.lib .row-title .ph {
    width: 40px; height: 40px; border-radius: 8px; object-fit: cover; background: var(--elev); flex: none;
  }
  table.lib a { color: var(--text); text-decoration: none; font-weight: 600; }
  table.lib a:hover { color: var(--accent); }
  .src { font-size: 11px; color: var(--faint); }
  @media (max-width: 980px) {
    .kpis, .split { grid-template-columns: 1fr 1fr; }
    .stamp { display: none; }
  }
  @media (max-width: 640px) {
    .kpis, .split { grid-template-columns: 1fr; }
    .top-inner, .wrap { padding-left: 16px; padding-right: 16px; }
  }
</style>
</head>
<body>
<div class="top">
  <div class="top-inner">
    <div class="mark">MS</div>
    <div class="brand">
      <h1>微软商店订单分析</h1>
      <p>已购游戏本体与 DLC · 原始数据保存在 raw/orders.json</p>
    </div>
    <div class="stamp" id="stamp"></div>
  </div>
</div>
<div class="wrap">
  <div class="kpis" id="kpis"></div>
  <div id="notice"></div>
  <div class="split">
    <section class="panel">
      <h2>花费</h2>
      <div class="hint">按币种分列，不可直接相加</div>
      <table class="data" id="spend"></table>
    </section>
    <section class="panel">
      <h2>排除项</h2>
      <div class="hint">未计入游戏库的订单行</div>
      <table class="data" id="skips"></table>
    </section>
  </div>
  <div class="lib-head">
    <div>
      <h2>游戏库</h2>
      <div class="sub">捆绑包已拆成游戏本体与 DLC，优先显示中文译名</div>
    </div>
  </div>
  <div class="toolbar">
    <button class="chip on" data-role="all">全部</button>
    <button class="chip" data-role="game">游戏本体</button>
    <button class="chip" data-role="dlc">DLC</button>
    <select id="currency"></select>
    <select id="sort">
      <option value="date-desc">日期 · 新到旧</option>
      <option value="date-asc">日期 · 旧到新</option>
      <option value="price-desc">价格 · 高到低</option>
      <option value="price-asc">价格 · 低到高</option>
      <option value="name-asc">名称 · A-Z</option>
    </select>
    <div class="views">
      <button class="view-btn on" data-view="table">表格</button>
      <button class="view-btn" data-view="cards">卡片</button>
    </div>
    <input id="q" placeholder="搜索中文名、英文名、SKU、订单号…">
    <span class="count" id="shown"></span>
  </div>
  <div id="board"></div>
</div>
<script>
const DATA = __DATA__;
const ROLE = {game:"游戏本体", dlc:"DLC"};
const SRC = {listing:"订单原文", "ms-catalog":"微软商店", steam:"Steam", bangumi:"Bangumi"};
const SKIP = {
  redemption_gift:"兑换赠品", promotional_zero:"0 元活动/奖励", consumable:"局内道具",
  "type=StoredValue":"账户余额", "type=CSV":"账户信用", "type=Software":"软件/订阅类",
  "type=Movie":"影片", "type=Unknown":"未知类型", "type=Subscription":"订阅",
  refunded_order:"退款订单", "order:isReturned":"已退回",
  "order:itemState=GiftRefunded":"礼品已退回", "order:itemState=Refunded":"已退款"
};
let role = "all", view = "table", currency = "", sortKey = "date-desc";

function money(n) {
  return Number(n || 0).toLocaleString("en-US", {maximumFractionDigits: 2});
}
function fmtPrice(it) {
  if (it.amount == null || it.amount === "" || Number(it.amount) === 0) return "—";
  return (it.currency ? it.currency + " " : "") + money(it.amount);
}
function cover(src, cls) {
  return src ? `<img class="${cls||"cover"}" src="${src}" alt="" loading="lazy">` : `<div class="${cls||"cover"} ph"></div>`;
}
function titleOf(it) { return it.display_name || it.zh_name || it.name || ""; }
function esc(s) { return String(s||""); }

function init() {
  const b = DATA.by_role || {};
  const kpis = [
    ["原始订单", DATA.raw_orders],
    ["库内条目", DATA.library_items, true],
    ["游戏本体", b.game || 0],
    ["DLC", b.dlc || 0],
    ["已排除", DATA.skipped_items]
  ];
  document.getElementById("kpis").innerHTML = kpis.map(([k,v,a]) =>
    `<div class="kpi${a?" accent":""}"><div class="k">${k}</div><div class="v">${v ?? 0}</div></div>`
  ).join("");
  document.getElementById("stamp").innerHTML =
    `生成 ${esc(DATA.generated_at||"—")}<br>抓取 ${esc(DATA.last_fetch_mode||"—")} ${esc(DATA.last_fetch_at||"")}`;
  if (!DATA.complete || DATA.truncated_pages) {
    document.getElementById("notice").innerHTML =
      `<div class="notice">数据可能不完整${DATA.truncated_pages ? "（曾有 " + DATA.truncated_pages + " 页被截断）" : ""}。最新订单：${esc(DATA.newest_submitted||"未知")}。可用 <code>python fetch_orders.py full</code> 补全。</div>`;
  }
  const spend = Object.entries(DATA.spend || {});
  const maxAmt = Math.max(1, ...spend.map(([,i]) => Number(i.amount||0)));
  document.getElementById("spend").innerHTML =
    "<tr><th>币种</th><th>订单</th><th class='num'>金额</th></tr>" +
    spend.map(([c,i]) => `<tr><td>${c}<div class="bar"><i style="width:${(100*Number(i.amount||0)/maxAmt).toFixed(1)}%"></i></div></td><td>${i.orders}</td><td class="num">${money(i.amount)}</td></tr>`).join("");
  document.getElementById("skips").innerHTML =
    "<tr><th>原因</th><th class='num'>条数</th></tr>" +
    Object.entries(DATA.skip_counts || {}).map(([k,v]) =>
      `<tr><td>${SKIP[k]||k}</td><td class="num">${v}</td></tr>`
    ).join("");
  const currencies = [...new Set((DATA.library||[]).map(x => x.currency).filter(Boolean))].sort();
  document.getElementById("currency").innerHTML =
    `<option value="">全部币种</option>` + currencies.map(c => `<option value="${c}">${c}</option>`).join("");
}

function filtered() {
  const q = (document.getElementById("q").value || "").trim().toLowerCase();
  let rows = (DATA.library || []).filter(it => {
    if (role !== "all" && it.role !== role) return false;
    if (currency && it.currency !== currency) return false;
    if (!q) return true;
    return [titleOf(it), it.zh_name, it.name, it.product_id, it.vanity, ...(it.from_bundles||[])].join(" ").toLowerCase().includes(q);
  });
  const [key, dir] = sortKey.split("-");
  const sign = dir === "asc" ? 1 : -1;
  rows.sort((a, b) => {
    if (key === "date") return sign * ((a.date_key||0) - (b.date_key||0)) || titleOf(a).localeCompare(titleOf(b), "zh");
    if (key === "price") {
      const aa = Number(a.amount||0), bb = Number(b.amount||0);
      if (aa === 0 && bb !== 0) return 1;
      if (bb === 0 && aa !== 0) return -1;
      const cur = (a.currency||"").localeCompare(b.currency||"");
      if (currency === "" && cur) return cur;
      return sign * (aa - bb) || (a.date_key||0) - (b.date_key||0);
    }
    return sign * titleOf(a).localeCompare(titleOf(b), "zh");
  });
  return rows;
}

function render() {
  const rows = filtered();
  document.getElementById("shown").textContent = `显示 ${rows.length} / ${DATA.library.length}`;
  const board = document.getElementById("board");
  if (!rows.length) { board.innerHTML = `<p class="empty">没有匹配的条目。</p>`; return; }
  if (view === "table") {
    board.innerHTML = `<div class="tbl-wrap"><table class="lib">
      <thead><tr>
        <th class="sortable" data-sort="name-asc">作品</th>
        <th>类型</th>
        <th class="sortable" data-sort="date-desc">日期</th>
        <th class="sortable num" data-sort="price-desc">价格</th>
        <th>SKU</th>
        <th>来源</th>
      </tr></thead>
      <tbody>${rows.map(it => {
        const shown = titleOf(it);
        const orig = (it.zh_name && it.name && it.zh_name !== it.name) ? `<div class="meta">${esc(it.name)}</div>` : "";
        const link = it.url ? `<a href="${it.url}" target="_blank" rel="noopener">${esc(shown)}</a>` : esc(shown);
        const bun = (it.from_bundles||[]).length ? `<div class="meta">捆绑包：${esc(it.from_bundles.join("、"))}</div>` : "";
        return `<tr>
          <td><div class="row-title">${cover(it.image,"cover")}<div>${link}${orig}${bun}</div></div></td>
          <td><span class="tag ${it.role}">${ROLE[it.role]||it.role}</span></td>
          <td>${esc(it.submitted||"—")}</td>
          <td class="num">${fmtPrice(it)}</td>
          <td class="meta">${esc(it.product_id||"—")}</td>
          <td class="src">${esc(SRC[it.zh_source]||it.zh_source||"—")}</td>
        </tr>`;
      }).join("")}</tbody></table></div>`;
    board.querySelectorAll("th.sortable").forEach(th => {
      th.addEventListener("click", () => {
        const next = th.getAttribute("data-sort");
        const sel = document.getElementById("sort");
        if (sel.value === next && next.endsWith("-desc")) sel.value = next.replace("desc","asc");
        else if (sel.value === next && next.endsWith("-asc")) sel.value = next.replace("asc","desc");
        else sel.value = next;
        sortKey = sel.value;
        render();
      });
    });
    return;
  }
  board.innerHTML = `<div class="grid">${rows.map(it => {
    const shown = titleOf(it);
    const orig = (it.zh_name && it.name && it.zh_name !== it.name) ? `<div class="meta">${esc(it.name)}</div>` : "";
    const link = it.url ? `<a href="${it.url}" target="_blank" rel="noopener">${esc(shown)}</a>` : esc(shown);
    const kids = (it.from_bundles||[]).map(b => `<span class="kid">捆绑包：${esc(b)}</span>`).join("");
    return `<article class="item">
      ${cover(it.image)}
      <div>
        <div><span class="tag ${it.role}">${ROLE[it.role]||it.role}</span><span class="meta">${esc(it.submitted||"")}</span></div>
        <h3>${link}</h3>
        ${orig}
        <div class="meta">${esc(it.product_id||"")} ${it.order_count>1?("· "+it.order_count+" 次"):""}</div>
        <div class="price">${fmtPrice(it)}</div>
      </div>
      ${kids ? `<div class="kids">${kids}</div>` : ""}
    </article>`;
  }).join("")}</div>`;
}

init();
render();
document.getElementById("q").addEventListener("input", render);
document.getElementById("sort").addEventListener("change", e => { sortKey = e.target.value; render(); });
document.getElementById("currency").addEventListener("change", e => { currency = e.target.value; render(); });
document.querySelectorAll(".chip").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".chip").forEach(b => b.classList.remove("on"));
    btn.classList.add("on");
    role = btn.dataset.role;
    render();
  });
});
document.querySelectorAll(".view-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".view-btn").forEach(b => b.classList.remove("on"));
    btn.classList.add("on");
    view = btn.dataset.view;
    render();
  });
});
</script>
</body>
</html>
"""
