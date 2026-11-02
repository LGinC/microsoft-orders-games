# 微软账户订单库

从微软账户「订单历史」接口抓取原始订单 JSON，再派生出已购买游戏、捆绑包和付费 DLC 列表，并按币种统计花费。

原始数据始终完整保留在本地 `raw/`，筛选规则可改、可重复跑。适合以后换口径（例如把 Game Pass 兑换也算进去）而不必重新抓包。

仓库不提交个人订单、支付信息和生成的 `result.html`。克隆后先抓取再处理即可。

## 目录

| 文件 / 目录 | 说明 |
| --- | --- |
| `raw/orders.json` | 原始订单，按 `orderId` 存储，抓取时只合并不删除 |
| `raw/catalog.json` | 商店 SKU 目录缓存（Game / Durable 等） |
| `raw/names.json` | 中文译名缓存（ms-catalog / steam / bangumi） |
| `raw/meta.json` | 每条订单首次写入、最近更新时间 |
| `raw/state.json` | 上次抓取模式、是否完整、截断页数 |
| `raw/fetch_log.jsonl` | 每一页抓取日志 |
| `rules.json` | 入库 / 排除规则 |
| `derived/library.json` | 已购游戏 / 捆绑包 / DLC |
| `derived/library.csv` | 同上，Excel 可开 |
| `derived/skipped.json` | 被排除的行及原因 |
| `derived/summary.json` | 条数与分币种花费 |
| `result.html` | 摘要 + 可搜索库列表 |
| `fetch_orders.py` | 抓取 / 增量更新原始数据 |
| `process_orders.py` | 从 raw 生成 derived 和 `result.html` |

## 环境

- Windows，已安装 Python 3
- 已安装 [BrowserSkill](https://github.com/Tencent/BrowserSkill) CLI（`bsk`，默认 `~\.local\bin\bsk.exe`）
- 浏览器已装 BrowserSkill 扩展，并开启本地连接
- 浏览器已登录 [微软账户订单页](https://account.microsoft.com/billing/orders)

在本目录打开终端即可运行下面的命令。

```text
python -m unittest tests.test_fetch_orders -v
```

## 生成报告

```text
python process_orders.py
```

会读取 `raw/orders.json` 和 `rules.json`，写出 `derived/` 与 `result.html`。用浏览器打开 `result.html` 即可查看摘要和库列表。

## 更新原始数据

订单列表新单在前。增量/全量都会在已登录的订单页里用 `fetch()` 直接拉 `/billing/orders/list`，**不再走 debug 抓包**，因此不会出现「截断页」（那是 8 MiB 抓包上限，不是接口失败）。已存在的 `orderId` 会整单覆盖（例如后来退款）。

```text
python fetch_orders.py incremental
python process_orders.py
```

第一次或库存为空时，增量会一直抓，直到接口不再返回 `continuationToken`。

补全更早、体积较大而被截断的页：

```text
python fetch_orders.py full
python process_orders.py
```

把当前 BrowserSkill debug 会话里已经抓到的 list 响应灌进 raw（不新开浏览器任务）：

```text
python fetch_orders.py import-capture
python process_orders.py
```

指定 session：

```text
python fetch_orders.py import-capture --session <session_id>
```

抓完必须再跑一次 `process_orders.py`，`result.html` 才会更新。

## 改分析口径

只编辑 `rules.json`，然后：

```text
python process_orders.py
```

不必重新抓取。当前默认：

- **捆绑包拆开**：从子项商店链接取出 SKU，调用 Display Catalog（`ProductType`：`Game` = 本体，`Durable` = DLC）。目录缓存在 `raw/catalog.json`，增量时只请求未知 SKU。
- **中文名**：优先微软商店中文列表、其次 Steam `l=schinese` 官方店名，再不行用 Bangumi `name_cn`。缓存在 `raw/names.json`。
- **入库**：去重后的游戏本体 + DLC。包装名本身不再占一条库记录；若子项全是 DLC 而父级是游戏，父级仍算本体。
- **整单排除**：退款（`Refunded` / `GiftRefunded` / `isReturned` / 文案含「已退款」「礼品已退回」）
- **行排除**：支付方式含「代码已兑换」且金额为 0 的兑换赠品；金额为 0 且无支付方式的活动奖励；`Consumable` 局内道具；账户余额、影片、订阅、Office 等

花费仍按 **订单去重、按币种分开**（捆绑包不把金额拆到每个 SKU 上）。

## 注意

- 旧版 debug 抓包留下的 `truncated_pages` 可用 `python fetch_orders.py full` 用页面 fetch 补全。
- `bsk` 会话默认约 5 分钟空闲会结束；增量抓取过程中不要长时间中断。
- 扩展必须连上本机 daemon（`bsk doctor` 里 extension connected 为通过）。
