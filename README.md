# DEX Monitor

Binance 永续 **涨幅榜自动发现** → **OI / 资金费率 / 大户多空** → **规则归因** → SQLite 持久化 → **Web 看板**（含 **股票价差监控** Tab）。

面向 alt 短时暴涨/暴跌监控（拉盘、轧空、清多、解锁供应等），默认优先 **BSC 链** 合约解析（CoinGecko）。

---

## 功能概览

| 模块 | 说明 |
|------|------|
| 动态选币 | 24h 涨幅榜 Top **10**（剔除 TradFi 代币化股票），`fixed_top_gainers` 可配 |
| 合约数据 | K 线、OI、资金费率、大户多空比、OI/市值 |
| 涨跌幅 | 15M/24H 来自 metrics + Binance ticker；**2D/3D 来自 Binance 1h K 线**（滚动 48h/72h） |
| 异常检测 | SURGE / DUMP / 杠杆过热 |
| 归因 | 轧空、拉盘、清多、解锁供应等 tags + 中文 narrative |
| 代币元数据 | CoinGecko 解析合约 → `token_metadata` 持久化 |
| 股票价差 | Binance / Hyperliquid / SoDEX 代币化股票标记价差 + 全球指数（可选 `spread_monitor`） |
| Web 看板 | 三 Tab：代币异常监控 / 股票价差监控 / 代币库；10s 刷新 |

---

## 环境要求

- Python **3.11+**
- 可访问 Binance API、CoinGecko API（部署环境需能出网）
- Linux 生产部署见 **[docs/DEPLOY.md](docs/DEPLOY.md)**

---

## 快速开始（本地）

```bash
git clone https://github.com/Dog-Feng/token-anomaly-monitor.git dex-monitor
cd dex-monitor

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
cp config.example.yaml config.yaml   # 首次
python main.py
```

浏览器打开：**http://127.0.0.1:8089**

一条命令同时启动：**数据采集 poll** + **Web 看板**（同进程，看板在后台线程）。

---

## 命令行

| 命令 | 说明 |
|------|------|
| `python main.py` | **默认**：poll + Web 看板 |
| `python main.py start` | 同上 |
| `python main.py poll` | 仅采集，不启动看板 |
| `python main.py web` | 仅看板（只读 SQLite） |
| `python main.py discover --top 10` | 查看当前涨幅榜候选 |
| `python main.py resolve CAKE` | 手动解析某币 BSC 合约 |
| `python main.py metadata` | 终端查看代币库 |
| `python main.py report --days 7` | 导出异常事件 CSV |
| `python main.py import-unlocks data/unlocks.example.json` | 导入解锁日历 |

---

## 配置说明

主配置文件：`config.yaml`（可从 `config.example.yaml` 复制）。

### 常用项

```yaml
discovery:
  enabled: true
  mode: dynamic              # dynamic | static | hybrid
  fixed_top_gainers: 10      # 剔除 TradFi 后取 24h 涨幅榜前 N
  exclude_tradfi: true       # 剔除代币化股票永续
  min_quote_volume_usdt: 5000000

coingecko:
  enabled: true
  chain_priority:
    - binance-smart-chain    # BSC 优先
    - base
    - ethereum

# 股票价差监控（默认关闭；启用后需 aiohttp / websockets / aiosqlite）
spread_monitor:
  enabled: false
  venues:
    binance: { enabled: true }
    hyperliquid: { enabled: true, dex: xyz }
    sodex: { enabled: true, ws_url: "wss://mainnet-gw.sodex.dev/ws/perps" }
  discovery:
    enabled: true
    min_venues: 2

web:
  enabled: true
  host: 127.0.0.1            # 公网部署改为 0.0.0.0，见 DEPLOY.md
  port: 8089

alert:
  telegram:
    enabled: false
    bot_token: ""
    chat_id: ""
```

### 数据文件

| 路径 | 说明 |
|------|------|
| `data/monitor.db` | SQLite 主库（metrics、异常、代币库） |
| `data/dsm.db` | 股票价差历史（`spread_monitor` 启用时） |
| `data/app.log` | 运行日志 |
| `data/wallets.json` | 链上 L1 监控地址（可选） |
| `data/cex_hot_wallets.json` | CEX 热钱包（可选） |

---

## Web 看板

访问 **http://127.0.0.1:8089**（或配置的 `web.host` / `web.port`）。

| Tab | 内容 |
|-----|------|
| **代币异常监控** | 一行一币（默认 **10** 个）：价格、15M/24H/**2D/3D**、费率、OI、OI÷市值、大户多空比、解锁看板、结论；支持排序；点击行查看归因 |
| **股票价差监控** | 代币化股票跨所标记价差 + 全球指数（需 `spread_monitor.enabled: true`） |
| **代币库** | `token_metadata` 持久化合约（BSC 优先） |

看板 **无登录鉴权**，只读 API；**不应在公网暴露写操作**（本项目无写接口）。

### API（只读）

| 路径 | 说明 |
|------|------|
| `GET /api/health` | 健康检查 |
| `GET /api/overview` | 统计与最近采集时间 |
| `GET /api/monitor-tokens?limit=10` | **主表数据**：指标 + 2D/3D + 结论 + narrative |
| `GET /api/spread/board` | 股票价差看板数据（quotes / indices / sync） |
| `GET /api/metrics` | 各 symbol 最新 metrics 快照 |
| `GET /api/anomalies?days=7` | 历史异常事件 |
| `GET /api/token-metadata` | 代币库 |

前端每 **10 秒**拉取；指标采集默认每 **60 秒**（`poll_interval_seconds`）。2D/3D 在每轮 poll 中通过 Binance **1h K 线**更新。

### 静态 UI 预览（无需 poll）

| 路径 | 说明 |
|------|------|
| `/preview` | 代币异常监控 Mock |
| `/spread-preview` | 股票价差监控 Mock |
| `web/static-demo.html` | 离线双击预览（旧版布局） |

### 重启与数据

历史数据在 **`data/monitor.db`**（SQLite）。正常重启 **不会清空** metrics、异常记录、代币库；进程启动时会从库恢复内存缓冲并继续采集。详见 [DEPLOY.md](docs/DEPLOY.md) 备份说明。

---

## 目录结构

```
dex-monitor/
├── main.py                 # 入口
├── config.yaml             # 运行配置
├── app/
│   ├── spread/             # 股票价差监控（Binance/HL/SoDEX）
│   ├── controllers/        # poll / detect / explain
│   ├── fetchers/
│   ├── models/
│   └── views/              # web_api、web_server、spread_api
├── web/                    # index.html、spread-preview、css/js
├── data/                   # SQLite + 日志（运行时生成）
├── deploy/                 # systemd 等部署模板
└── docs/
    ├── DESIGN.md
    └── DEPLOY.md
```

---

## 测试

```bash
pytest tests/ -q
```

---

## 文档索引

- [设计文档](docs/DESIGN.md)
- **[Linux 公网部署](docs/DEPLOY.md)** ← 生产环境必读

---

## 免责声明

本工具仅供数据监控与研究，不构成投资建议。公网开放看板会暴露监控数据，请自行评估风险，详见部署文档安全说明。
