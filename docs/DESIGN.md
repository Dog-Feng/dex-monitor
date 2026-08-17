# Token Anomaly Monitor — 设计开发文档

> 版本：v0.1  
> 目标：监控代币短时暴涨/暴跌，结合合约数据与定向链上数据做可解释归因。  
> 原则：小架构、单机可跑、SQLite 存储、轻量 MVC，不做全链扫描。

---

## 1. 背景与范围

### 1.1 业务目标

发现永续合约代币的 **价格异常**（暴涨/暴跌），并结合以下信息给出 **结构化归因**：

| 层级 | 数据来源 | 作用 |
|------|---------|------|
| **第一层** | CEX：K 线、成交量、OI、资金费率、大户多空比、流通市值 | 识别拉盘、轧空、清多/清多等场内博弈 |
| **第二层** | 定向链上：团队/解锁地址、CEX 充值、watchlist 代币 Transfer | 识别解锁砸盘、项目方出货、高位现货抛压 |

### 1.2 明确不做

- 全链 / 全代币扫描
- 消息队列（Kafka、Redis Streams 等）
- Redis、独立时序库、任务调度平台（Airflow 等）
- 与 BTC 的 beta /  sector 中性分析（alt 异动以场内博弈为主）
- ML 模型（初期仅用规则引擎）

### 1.3 典型场景（检测 + 归因）

| 场景 | 第一层信号 | 第二层信号 |
|------|-----------|-----------|
| 拉盘逼空 | 价↑、量↑、OI↓ 或 OI↑、正费率 | 链上无明显 CEX 流入 |
| 高位轧空末段 | 价高、负费率极值（如 -1%/-2%）、OI 震荡 | 无 CEX 流入 → 纯合约；有流入 → 出货区 |
| 砸盘清多 | 价↓、量↑、OI↓、费率曾偏正 | — |
| 解锁/项目方砸盘 | 价↓、量↑ | 解锁/团队地址大额转出 → CEX |
| 拉高出货 | 价↑、正费率、随后价跌 | 拉升后团队地址 → CEX 充值 |

---

## 2. 架构概览

### 2.1 轻量 MVC

```
┌──────────────────────────────────────────────────────────────┐
│                        main.py（入口）                        │
│                   启动轮询循环 / CLI 子命令                    │
└──────────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  Model          │  │  Controller     │  │  View           │
│  数据 + 持久化   │  │  业务流程编排    │  │  输出展示        │
├─────────────────┤  ├─────────────────┤  ├─────────────────┤
│ entities        │  │ poll_controller │  │ console_view    │
│ repositories    │  │ detect_ctrl     │  │ report_view     │
│ sqlite.py       │  │ explain_ctrl    │  │ (可选 export)   │
│ fetchers        │  │ alert_ctrl      │  │                 │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

**职责划分：**

| 层 | 职责 | 禁止 |
|----|------|------|
| **Model** | 实体定义、SQLite 读写、外部 API 原始拉取 | 业务判断、打印输出 |
| **Controller** | 轮询编排、异常检测、归因、告警触发 | 直接 SQL、直接 print |
| **View** | 控制台表格、事件摘要、JSON/CSV 导出 | 访问 API、写库 |

### 2.2 运行形态

- **单进程**：`while True` + `time.sleep(interval)` 轮询
- **配置**：`config.yaml`（阈值、watchlist、API key、链 RPC）
- **部署**：本地 `python main.py`；生产可用 systemd / Windows 任务计划 `run_on_startup`
- **CLI**（可选）：`python main.py poll` | `report --days 7` | `backfill --symbol XXX`

### 2.3 数据流

```
config.yaml
    │
    ▼
[Fetch CEX] ──► metrics 表（周期性快照）
[Fetch Chain L1/L2] ──► onchain_events 表
    │
    ▼
[Detect] ──► 读取最近 N 根 metrics + 内存 deque
    │
    ▼
[Explain] ──► 对齐 onchain_events + unlocks 表
    │
    ▼
[Alert + View] ──► anomaly_events 表 + 控制台 / Telegram
```

---

## 3. 目录结构

```
token-anomaly-monitor/
├── main.py                      # 入口：解析 CLI，启动 Controller
├── config.yaml                  # 运行配置（示例见 config.example.yaml）
├── config.example.yaml
├── requirements.txt
├── docs/
│   └── DESIGN.md                # 本文档
├── app/
│   ├── __init__.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── entities.py          # dataclass：MetricSnapshot, AnomalyEvent, OnchainEvent
│   │   ├── sqlite.py              # 连接、建表、迁移
│   │   └── repositories.py        # CRUD：metrics / events / onchain / unlocks
│   ├── fetchers/
│   │   ├── __init__.py
│   │   ├── binance.py             # K线、OI、资金费率、大户多空比
│   │   ├── market_cap.py          # CoinGecko 等流通市值（可缓存到 metrics）
│   │   └── chain/
│   │       ├── rpc_client.py      # ETH/BSC 等 JSON-RPC 封装
│   │       ├── address_watch.py   # L1：指定地址转出、余额变化
│   │       └── token_transfer.py  # L2：watchlist 代币 Transfer 过滤
│   ├── controllers/
│   │   ├── __init__.py
│   │   ├── poll_controller.py     # 主轮询：拉数 → 存库 → 检测 → 归因 → 告警
│   │   ├── detect_controller.py   # 异常检测规则
│   │   ├── explain_controller.py  # 归因规则
│   │   └── alert_controller.py    # 去重 + Telegram / 控制台
│   ├── views/
│   │   ├── __init__.py
│   │   ├── console_view.py        # 表格 / 彩色摘要
│   │   └── export_view.py         # 导出 CSV / JSONL
│   └── services/
│       ├── history_buffer.py      # 内存 deque：每 symbol 最近 288 根 5m bar
│       ├── rate_limiter.py        # API 限流 sleep
│       └── dedup.py               # 告警去重（同 symbol + 同 type，N 分钟内一次）
├── data/
│   ├── monitor.db                 # SQLite（gitignore）
│   ├── wallets.json               # L1 监控地址（团队/解锁/CEX 标签）
│   └── cex_hot_wallets.json       # 已知 CEX 充值地址
└── scripts/
    └── init_db.py                 # 初始化表结构
```

---

## 4. 第一层：CEX 合约数据

### 4.1 数据源（MVP：Binance USDT 永续）

| 数据 | API | 频率 |
|------|-----|------|
| 5m K 线（OHLCV） | `GET /fapi/v1/klines` | 每 1～5 min |
| 未平仓量 OI | `GET /fapi/v1/openInterest` | 与 K 线同频 |
| 资金费率 | `GET /fapi/v1/premiumIndex` 或 funding 历史 | 同频 |
| 大户账户多空比 | `GET /futures/data/topLongShortAccountRatio` | 每 5 min |
| 流通市值 | CoinGecko ` /coins/markets` | 每 1h 缓存 |

### 4.2 内存结构

每个 `symbol` 维护 `HistoryBuffer`（`collections.deque`，maxlen=288，约 24h 5m 数据）：

- 用于计算：15m/30m 涨跌幅、均量、OI 变化率、费率变化
- **不必**为全量历史查库；库内 metrics 用于复盘与重启恢复

### 4.3 检测规则（可调，默认值）

规则在 `detect_controller.py` 中实现，输出 `AnomalyType` 枚举。

#### 暴涨 `SURGE`

```
change_15m >= surge_pct          # 默认 8%
AND volume_15m >= vol_multiplier * avg_volume_24h   # 默认 3x
AND (
      oi_change_30m <= -oi_drop_pct                 # 默认 -5%，轧空
   OR funding_rate >= funding_positive_threshold   # 默认 0.05%，拉盘
   OR oi_change_30m >= oi_rise_pct                 # 默认 5%，追多
)
```

#### 暴跌 `DUMP`

```
change_15m <= -dump_pct          # 默认 -8%
AND volume_15m >= vol_multiplier * avg_volume_24h
AND (
      oi_change_30m <= -oi_drop_pct                 # 清多
   OR funding_rate >= funding_extreme_positive      # 默认 0.1%
)
```

#### 杠杆过热预警 `LEVERAGE_HEAT`（仅记录，可选告警）

```
oi_mcap_ratio >= oi_mcap_ratio_warn    # 默认 0.2
AND 未在 cooldown 内
```

#### 严重程度 `severity`

```
base = HIGH if abs(change_15m) >= 12% else MEDIUM
if oi_mcap_ratio >= 0.3: base = HIGH
if funding_rate <= -0.005 or >= 0.01: base = HIGH   # -0.5% / 1% 量级
```

### 4.4 归因规则（第一层）

`explain_controller.py` 根据检测时刻快照生成 `tags` 与 `narrative`：

| 条件组合 | tag | 说明 |
|---------|-----|------|
| 价↑ OI↓ 费率≤0 | `short_squeeze` | 轧空/逼空 |
| 价↑ OI↑ 费率>0 | `long_chase` | 正费率拉盘、追多 |
| 价↑ 费率≤-0.005 | `extreme_neg_funding_top` | 高位极端负费率 |
| 价↓ OI↓ 费率>0 | `long_liquidation` | 多头清算 |
| 价↓ OI↑ 费率<0 | `short_build` | 加空砸盘 |
| OI/市值 > 阈值 | `high_leverage` | 小盘高杠杆 |

---

## 5. 第二层：定向链上数据

### 5.1 L1 — 地址级监控（高优先级）

**监控对象**（`data/wallets.json`）：

```json
{
  "wallets": [
    {
      "address": "0x...",
      "chain": "ethereum",
      "label": "team_treasury",
      "tokens": ["0xTokenContract..."],
      "symbol": "ABC"
    }
  ]
}
```

**监控逻辑**（`address_watch.py`）：

1. 每 `chain_poll_interval`（默认 300s）查询 watch 地址相关 tx
2. 解析 ERC20 `Transfer`：from = watch 地址，value > `min_transfer_usd`
3. 若 `to` ∈ `cex_hot_wallets.json` → 事件类型 `CEX_DEPOSIT`
4. 若 from 为 vesting/unlock 标签 → `UNLOCK_TRANSFER`
5. 写入 `onchain_events` 表

**API 选型**（任选，可配置）：

- Etherscan / BscScan `account tokentx`（简单、有 rate limit）
- 或 JSON-RPC `eth_getLogs` 按 address 过滤（需自建 fromBlock 游标）

### 5.2 L2 — watchlist 代币 Transfer 索引

**范围**：仅 `config.yaml` 中 watchlist 里代币的 **合约地址**，不做全链。

**逻辑**（`token_transfer.py`）：

1. 维护每链 `last_scanned_block`
2. 每轮拉取 `Transfer(from, to, value)` logs，`address = token_contract`
3. 过滤：
   - `value_usd >= min_transfer_usd`（默认 10_000）
   - `to` ∈ CEX 热钱包 **或** `from` ∈ 已知 team/vesting
4. 写入 `onchain_events`

**异动 lazy 模式**（省 API）：

- 常规：仅跑 L1 地址监控
- 当第一层检测到 `SURGE`/`DUMP` 时，对该 `symbol` 触发 L2 **回溯 24h** Transfer 扫描

### 5.3 链上归因（与 anomaly 对齐）

检测异常时刻 `T`，查询 `onchain_events` where `symbol` match AND `ts BETWEEN T-6h AND T+1h`：

| 链上事件 | 叠加效果 |
|---------|---------|
| `CEX_DEPOSIT` 且 label=team | tag `team_dump`，置信度 +0.35 |
| `UNLOCK_TRANSFER` | tag `unlock_sell_pressure`，+0.30 |
| `CEX_DEPOSIT` 大额 | tag `spot_sell_pressure`，+0.25 |
| 无链上事件 | 维持纯合约归因 |

最终 `narrative` 示例：

```
ABC 15m +11.2% | OI 30m -8.1% | 费率 -0.06% → 轧空
链上：2h 内团队地址向 Binance 充值 120 万枚 → 警惕拉高出货
```

---

## 6. SQLite 设计

文件路径：`data/monitor.db`  
WAL 模式：`PRAGMA journal_mode=WAL;`

### 6.1 表结构

#### `symbols` — watchlist

| 列 | 类型 | 说明 |
|----|------|------|
| symbol | TEXT PK | 如 `BTCUSDT` |
| base_asset | TEXT | `BTC` |
| chain | TEXT | `ethereum` / `bsc` / null |
| token_contract | TEXT | L2 用 |
| coingecko_id | TEXT | 市值查询 |
| enabled | INTEGER | 1/0 |

#### `metrics` — 第一层快照

| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER PK | |
| ts | INTEGER | Unix 秒 |
| symbol | TEXT | |
| price | REAL | |
| volume_5m | REAL | |
| oi | REAL | 未平仓量（张或 USDT 名义，统一口径） |
| funding_rate | REAL | 当前/最近费率 |
| whale_long_short_ratio | REAL | 大户多空比 |
| market_cap | REAL | 流通市值 USD |
| oi_mcap_ratio | REAL | 衍生字段 |

索引：`(symbol, ts)`  
保留策略：原始 5m 快照保留 **30 天**，超期 `DELETE`（启动时或每日一次清理）。

#### `unlocks` — 解锁日历

| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER PK | |
| symbol | TEXT | |
| unlock_ts | INTEGER | |
| amount | REAL | 解锁数量 |
| pct_circulating | REAL | 占流通比 |
| source | TEXT | 手工 / tokenunlocks |
| note | TEXT | |

#### `onchain_events` — 第二层事件

| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER PK | |
| ts | INTEGER | 链上 block time |
| chain | TEXT | |
| symbol | TEXT | |
| event_type | TEXT | `CEX_DEPOSIT` / `UNLOCK_TRANSFER` / `LARGE_TRANSFER` |
| from_address | TEXT | |
| to_address | TEXT | |
| amount | REAL | token 数量 |
| amount_usd | REAL | 估算 |
| tx_hash | TEXT UNIQUE | 防重 |
| label | TEXT | team / vesting / unknown |
| raw_json | TEXT | 可选 |

索引：`(symbol, ts)`，`tx_hash`

#### `anomaly_events` — 异常与归因结果

| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER PK | |
| detected_ts | INTEGER | |
| symbol | TEXT | |
| anomaly_type | TEXT | SURGE / DUMP / LEVERAGE_HEAT |
| severity | TEXT | LOW / MEDIUM / HIGH |
| change_15m | REAL | |
| metrics_json | TEXT | 快照 JSON |
| tags_json | TEXT | `["short_squeeze","team_dump"]` |
| narrative | TEXT | 人类可读摘要 |
| onchain_refs_json | TEXT | 关联 onchain_events id 列表 |

索引：`(symbol, detected_ts)`，`detected_ts`

#### `alert_log` — 告警去重

| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER PK | |
| symbol | TEXT | |
| anomaly_type | TEXT | |
| sent_ts | INTEGER | |

### 6.2 实体（Model 层 dataclass  sketch）

```python
@dataclass
class MetricSnapshot:
    ts: int
    symbol: str
    price: float
    volume_5m: float
    oi: float
    funding_rate: float
    whale_long_short_ratio: float | None
    market_cap: float | None
    oi_mcap_ratio: float | None

@dataclass
class AnomalyEvent:
    detected_ts: int
    symbol: str
    anomaly_type: str
    severity: str
    change_15m: float
    metrics: MetricSnapshot
    tags: list[str]
    narrative: str
    onchain_refs: list[int]
```

---

## 7. Controller 流程

### 7.1 主轮询 `PollController.run_once()`

```
1. symbols = repo.load_enabled_symbols()
2. for symbol in symbols:
     a. metrics = binance_fetcher.fetch(symbol)
     b. repo.insert_metrics(metrics)
     c. buffer.push(symbol, metrics)
3. if should_fetch_market_cap(): 批量更新 market_cap
4. chain L1: address_watch.scan() → repo.insert_onchain_events()
5. for symbol in symbols:
     a. event = detect_controller.evaluate(symbol, buffer)
     b. if event:
          - onchain = explain_controller.fetch_related_onchain(event)
          - if lazy L2: token_transfer.backfill_24h(symbol)
          - explain_controller.enrich(event, onchain, unlocks)
          - if alert_controller.should_send(event):
               alert_controller.send(event)
               view.render(event)
          - repo.insert_anomaly_event(event)
6. repo.cleanup_old_metrics(retention_days=30)
```

### 7.2 告警去重

`dedup.py`：同一 `(symbol, anomaly_type)` 在 `cooldown_minutes`（默认 30）内不重复推送；仍写入 `anomaly_events` 供复盘。

### 7.3 配置项 `config.yaml`

```yaml
poll_interval_seconds: 60
kline_interval: "5m"

detection:
  surge_pct: 0.08
  dump_pct: 0.08
  vol_multiplier: 3.0
  oi_drop_pct: 0.05
  oi_rise_pct: 0.05
  funding_positive_threshold: 0.0005
  funding_extreme_positive: 0.001
  oi_mcap_ratio_warn: 0.2

chain:
  enabled: true
  l1_interval_seconds: 300
  l2_mode: lazy          # lazy | always
  min_transfer_usd: 10000
  etherscan_api_key: ""
  bscscan_api_key: ""

alert:
  cooldown_minutes: 30
  telegram:
    enabled: false
    bot_token: ""
    chat_id: ""

sqlite:
  path: data/monitor.db
  metrics_retention_days: 30
```

---

## 8. View 层

### 8.1 控制台输出

异常时打印：

```
[2026-08-17 20:15:00] HIGH | SURGE | ABCUSDT | +11.2% (15m)
  OI 30m: -8.1% | Funding: -0.062% | OI/MCap: 0.31
  Tags: short_squeeze, high_leverage
  链上: 团队地址 2h 前充值 CEX 1.2M ABC
  → 价涨+OI降+负费率，典型轧空；需警惕团队出货
```

### 8.2 导出

- `python main.py report --days 7` → `data/reports/events_YYYYMMDD.csv`
- 字段：时间、symbol、类型、涨跌幅、tags、narrative

---

## 9. 非功能需求

| 项 | 要求 |
|----|------|
| 容错 | 单 symbol 拉取失败 skip，不中断整轮 |
| 限流 | Binance / 浏览器 API 请求间隔 ≥ 100ms；429 指数退避 |
| 日志 | `logging` 标准库，文件 `data/app.log`，按日轮转（可选） |
| 安全 | API key 仅放 config.yaml，加入 `.gitignore` |
| 测试 | 核心 `detect_controller`、`explain_controller` 单元测试（pytest） |

---

## 10. 开发计划

### Phase 1 — 骨架 + 第一层（约 1 周）

| 任务 | 产出 |
|------|------|
| 项目脚手架、MVC 目录、`config.example.yaml` | 可运行空轮询 |
| SQLite 建表、`repositories` | `init_db.py` |
| Binance fetcher + `HistoryBuffer` | metrics 入库 |
| `detect_controller` 暴涨/暴跌规则 | 控制台告警 |
| `console_view` + `anomaly_events` 落库 | 最小闭环 |

**验收**：10 个 symbol 跑 24h，能检测并记录 SURGE/DUMP。

### Phase 2 — 归因增强 + 解锁（约 3～5 天）

| 任务 | 产出 |
|------|------|
| CoinGecko 市值 + OI/市值比 | LEVERAGE_HEAT |
| `unlocks` 表 + 手工 JSON 导入 | 解锁日前后 tag |
| `explain_controller` 完整规则表 | tags + narrative |
| 告警去重 + 可选 Telegram | 不刷屏 |

**验收**：异常事件带 tags 与 narrative；解锁日前 DUMP 有 `unlock_sell_pressure`。

### Phase 3 — 第二层链上（约 1 周）

| 任务 | 产出 |
|------|------|
| `wallets.json` / `cex_hot_wallets.json` 模板 | 配置文档 |
| L1 `address_watch` | onchain_events 入库 |
| L2 lazy `token_transfer` | 异动触发 24h 回溯 |
| explain 链上对齐 | narrative 含链上句 |

**验收**：模拟或真实 team→CEX 转账能在 DUMP/SURGE 归因中出现。

### Phase 4 —  polish（可选）

- CLI `report` 导出
- 重启时从 metrics 恢复 `HistoryBuffer`
- pytest 覆盖检测/归因规则
- README 运行说明

---

## 11. 依赖

```
requests>=2.31.0
pyyaml>=6.0
```

可选：`python-telegram-bot` 或裸 `requests` 调 Telegram API。

Python **3.11+**（使用 `list[str]` 等类型注解）。

---

## 12. 风险与限制

1. **Binance 单所**：部分小币仅在 OKX/Bybit，后续可加 fetcher 接口抽象。
2. **链上标签**：团队地址需人工维护，错标导致误报。
3. **市值延迟**：CoinGecko 更新慢，OI/市值比仅作参考。
4. **费率口径**：各所计算不同，MVP 统一 Binance。
5. **lazy L2**：极端快速砸盘可能链上稍晚于价格；L1 地址监控仍应常开。

---

## 13. 扩展点（保持 MVC，不增中间件）

| 扩展 | 做法 |
|------|------|
| 多加一所 | 新 fetcher 实现同一 interface，`poll_controller` 注入 |
| Web 看板 | 新增 `views/web_view.py`（Flask 只读查 SQLite） |
| 人工标注 | `anomaly_events` 加 `user_note` 列 |
| OKX 同步 | `fetchers/okx.py` |

---

## 14. 附录：规则参数 tuning 建议

1. 先用默认阈值跑 3～7 天，统计 `anomaly_events` 数量/日。
2. 若误报多：提高 `surge_pct` 或 `vol_multiplier`。
3. 若漏报小盘：对 `oi_mcap_ratio > 0.25` 的 symbol 单独降低阈值（symbol 级 override，后期可加）。
4. 负费率 `-1%/-2%` 场景：将 `funding_extreme` 设为 `-0.01` 单独打 tag，severity 升为 HIGH。

---

*文档结束 — 实现时以 `config.example.yaml` 与 `app/models/sqlite.py` 为准。*
