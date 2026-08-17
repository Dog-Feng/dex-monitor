# Token Anomaly Monitor

Binance 永续 **涨幅榜自动发现** → **OI / 资金费率 / 大户多空** → **规则归因** → SQLite 持久化 → **Web 看板**。

面向 alt 短时暴涨/暴跌监控（拉盘、轧空、清多、解锁供应等），默认优先 **BSC 链** 合约解析（CoinGecko）。

---

## 功能概览

| 模块 | 说明 |
|------|------|
| 动态选币 | Binance 24h 涨/跌幅 Top N → 15m 涨跌幅二次筛选 |
| 合约数据 | K 线、OI、资金费率、大户多空比、OI/市值 |
| 异常检测 | SURGE / DUMP / 杠杆过热 |
| 归因 | 轧空、拉盘、清多、解锁供应等 tags + 中文 narrative |
| 代币元数据 | CoinGecko 解析合约 → `token_metadata` 持久化 |
| Web 看板 | 暗色总控台 UI（参考 [dex-grid](https://github.com/Dog-Feng/dex-grid/tree/main/web)） |

---

## 环境要求

- Python **3.11+**
- 可访问 Binance API、CoinGecko API（部署环境需能出网）
- Linux 生产部署见 **[docs/DEPLOY.md](docs/DEPLOY.md)**

---

## 快速开始（本地）

```bash
git clone <your-repo> token-anomaly-monitor
cd token-anomaly-monitor

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
| `python main.py discover --top 30` | 查看当前涨幅榜候选 |
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
  min_change_15m: 0.03       # 15m |涨跌| >= 3% 才分析
  min_quote_volume_usdt: 5000000

coingecko:
  enabled: true
  chain_priority:
    - binance-smart-chain    # BSC 优先
    - base
    - ethereum

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
| `data/monitor.db` | SQLite 主库 |
| `data/app.log` | 运行日志 |
| `data/wallets.json` | 链上 L1 监控地址（可选） |
| `data/cex_hot_wallets.json` | CEX 热钱包（可选） |

---

## Web 看板

| Tab | 内容 |
|-----|------|
| 总览 | 24h 异常统计、最近事件 |
| 异常事件 | 列表 + 点击归因详情 |
| 实时指标 | 各币最新价、费率、OI |
| 代币库 | 持久化合约地址 |

看板 **无登录鉴权**，只读 API；**不应在公网暴露写操作**（本项目无写接口）。

---

## 目录结构

```
token-anomaly-monitor/
├── main.py                 # 入口
├── config.yaml             # 运行配置
├── app/                    # MVC 业务代码
├── web/                    # 看板静态页
├── data/                   # SQLite + 日志（运行时生成）
├── deploy/                 # systemd 等部署模板
└── docs/
    ├── DESIGN.md           # 设计文档
    └── DEPLOY.md           # Linux 公网部署
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
