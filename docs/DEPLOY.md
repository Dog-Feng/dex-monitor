# Linux 公网部署指南

本文说明在 Linux 服务器上部署 **Token Anomaly Monitor**，并通过公网 **无鉴权** 访问 Web 看板（默认端口 **8089**）。

> **安全提示**：看板无用户名/密码，任何能访问 URL 的人均可查看监控数据。请勿在页面或 API 中暴露 API Key；`config.yaml` 不要提交到公开仓库。

---

## 1. 架构说明

```
                    公网用户浏览器
                          │
                          ▼
              ┌───────────────────────┐
              │  0.0.0.0:8089         │  ← Flask 看板（只读）
              │  token-anomaly-monitor │
              │  ├─ Web 线程           │
              │  ├─ Poll 主线程        │  → Binance / CoinGecko
              │  └─ Spread 后台线程*   │  → Binance / HL / SoDEX WS
              └───────────┬───────────┘
                          │
          ┌───────────────┴───────────────┐
          ▼                               ▼
   data/monitor.db                  data/dsm.db*
```

\* `spread_monitor.enabled: true` 时启动价差监控；`dsm.db` 可选持久化。

- **单进程**：`python main.py` = poll + Web +（可选）spread 后台 asyncio
- **看板 API 只读**：`/api/*` 仅查询内存/SQLite，无写接口
- **公网访问**：配置 `web.host: 0.0.0.0`，防火墙放行端口
- **三 Tab**：代币异常监控（默认 10 币）| 股票价差监控 | 代币库

---

## 2. 服务器要求

| 项目 | 建议 |
|------|------|
| OS | Ubuntu 22.04 / Debian 12 / CentOS Stream 9 等 |
| CPU / 内存 | 1 核 / 512MB 起（建议 1GB） |
| 磁盘 | ≥ 5GB（SQLite + 日志增长） |
| Python | 3.11+ |
| 网络 | 出网访问 `fapi.binance.com`、`api.coingecko.com`；价差需 HL/SoDEX WebSocket |

---

## 3. 部署步骤

### 3.1 创建运行用户与目录

```bash
sudo useradd -r -m -d /opt/token-anomaly-monitor -s /bin/bash tam
sudo mkdir -p /opt/token-anomaly-monitor
sudo chown tam:tam /opt/token-anomaly-monitor
```

### 3.2 上传代码

```bash
# 示例：git 克隆（替换为你的仓库地址）
sudo -u tam git clone <your-repo-url> /opt/token-anomaly-monitor/app-src
# 或 scp/rsync 将项目复制到 /opt/token-anomaly-monitor/
```

以下假设项目根目录为 **`/opt/token-anomaly-monitor`**。

### 3.3 Python 虚拟环境

```bash
cd /opt/token-anomaly-monitor
sudo -u tam python3 -m venv .venv
sudo -u tam .venv/bin/pip install -U pip
sudo -u tam .venv/bin/pip install -r requirements.txt
```

### 3.4 配置文件

```bash
sudo -u tam cp config.example.yaml config.yaml
sudo -u tam nano config.yaml
```

**公网无鉴权访问** 必须修改：

```yaml
web:
  enabled: true
  host: 0.0.0.0          # 监听所有网卡，允许公网连入
  port: 8089

logging:
  level: INFO
  file: data/app.log

sqlite:
  path: data/monitor.db
```

可选：Telegram 告警、链上 `chain.enabled`、CoinGecko Pro `api_key`、`spread_monitor`（价差 Tab）等见 `config.example.yaml`。

生产建议同步：

```yaml
discovery:
  fixed_top_gainers: 10

spread_monitor:
  enabled: true   # 不需要价差 Tab 时可 false
```

### 3.5 首次启动验证

```bash
cd /opt/token-anomaly-monitor
sudo -u tam .venv/bin/python main.py
```

本机测试：

```bash
curl -s http://127.0.0.1:8089/api/health
curl -s http://127.0.0.1:8089/api/overview
curl -s "http://127.0.0.1:8089/api/monitor-tokens?limit=10" | head -c 500
curl -s http://127.0.0.1:8089/api/spread/board | head -c 500
```

浏览器：`http://<服务器公网IP>:8089`（代币 Tab）、`/spread-preview` 可单独预览价差 UI。

确认正常后 `Ctrl+C` 停止，改用 systemd 托管。

---

## 4. systemd 常驻（推荐）

项目自带 unit 模板：`deploy/token-anomaly-monitor.service`

```bash
sudo cp /opt/token-anomaly-monitor/deploy/token-anomaly-monitor.service /etc/systemd/system/
# 若安装路径不是 /opt/token-anomaly-monitor，请编辑 unit 内 WorkingDirectory / ExecStart
sudo systemctl daemon-reload
sudo systemctl enable token-anomaly-monitor
sudo systemctl start token-anomaly-monitor
sudo systemctl status token-anomaly-monitor
```

常用运维：

```bash
sudo journalctl -u token-anomaly-monitor -f     # 跟踪日志
sudo systemctl restart token-anomaly-monitor
```

应用日志文件：`/opt/token-anomaly-monitor/data/app.log`

---

## 5. 防火墙放行

### UFW（Ubuntu / Debian）

```bash
sudo ufw allow 8089/tcp comment 'token-anomaly-monitor'
sudo ufw reload
sudo ufw status
```

### firewalld（CentOS / RHEL）

```bash
sudo firewall-cmd --permanent --add-port=8089/tcp
sudo firewall-cmd --reload
```

### 云厂商安全组

在阿里云 / 腾讯云 / AWS 控制台，为实例 **入站** 放行 **TCP 8089**（来源按需设为 `0.0.0.0/0` 即全网可访问）。

---

## 6. 公网访问地址

部署完成后：

```
http://<公网IP>:8089
```

示例：`http://123.45.67.89:8089`

无用户名密码，打开即可查看总览、异常事件、指标、代币库。

---

## 7. 可选：Nginx 反向代理（80/443 端口）

若希望用域名 + 80 端口访问，可在本机 Nginx 反代到 8089（仍无鉴权）：

```nginx
server {
    listen 80;
    server_name monitor.example.com;

    location / {
        proxy_pass http://127.0.0.1:8089;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

此时 `config.yaml` 可改回：

```yaml
web:
  host: 127.0.0.1    # 只监听本机，由 Nginx 对外
  port: 8089
```

防火墙只放行 80/443，**不**对公网直接暴露 8089。

> 如需 HTTPS，使用 certbot / acme 申请证书。仍 **无应用层鉴权**；若需密码保护，须在 Nginx 增加 `auth_basic`（本项目的默认设计是不鉴权）。

---

## 8. 升级与备份

### 升级

```bash
cd /opt/token-anomaly-monitor
sudo systemctl stop token-anomaly-monitor
sudo -u tam git pull   # 或重新 rsync 代码
sudo -u tam .venv/bin/pip install -r requirements.txt
sudo systemctl start token-anomaly-monitor
```

### 备份 SQLite

```bash
sudo -u tam cp /opt/token-anomaly-monitor/data/monitor.db \
  /opt/token-anomaly-monitor/data/monitor.db.bak.$(date +%F)
```

建议 cron 每日备份 `data/monitor.db` 与 `config.yaml`。

**重启说明**：`systemctl restart` 或升级后重启 **不会清空** SQLite 中的 metrics、异常事件、代币库；进程会从 `data/monitor.db` 恢复历史并继续 poll。勿删除或覆盖 `data/` 目录即可保留数据。

---

## 9. 故障排查

| 现象 | 排查 |
|------|------|
| 公网无法访问 | 检查 `web.host` 是否为 `0.0.0.0`；安全组/UFW 是否放行 8089 |
| 看板无法访问 / ModuleNotFoundError: waitress | 升级后执行 `pip install -r requirements.txt` 并 `systemctl restart` |
| 看板无数据 | `systemctl status` 是否正常；`data/app.log` 是否有 Binance 请求错误 |
| CoinGecko 429 | 免费 API 限流；增大 `coingecko.metadata_refresh_hours` 或配置 Pro Key |
| 端口被占用 | `ss -lntp \| grep 8089` 换 `web.port` 或停止冲突进程 |

健康检查：

```bash
curl http://127.0.0.1:8089/api/health
# {"status":"ok","db":"data/monitor.db"}
```

---

## 10. 安全说明（公网无鉴权）

本系统按你的要求 **不提供登录/Token 鉴权**。请注意：

1. **只读暴露**：他人仅能查看监控数据，无法通过 API 修改配置或交易。
2. **敏感配置**：`config.yaml` 中的 Telegram Token、Etherscan Key 等不会出现在 API 响应中，但服务器被入侵仍可能泄露，请限制 SSH 访问。
3. **最小暴露**：若仅个人使用，可用 VPN / IP 白名单代替全网 `0.0.0.0/0`。
4. **不要用 root 运行**：systemd 已配置 `User=tam`。

若将来需要鉴权，建议在 Nginx 层加 Basic Auth，或 fronting 内网 + Tailscale，而不是改业务代码。

---

## 11. 快速命令备忘

```bash
# 安装依赖
cd /opt/token-anomaly-monitor && .venv/bin/pip install -r requirements.txt

# 前台调试
.venv/bin/python main.py

# 生产托管
sudo systemctl start token-anomaly-monitor

# 查看涨幅榜（SSH 上）
.venv/bin/python main.py discover --top 20
```

---

部署完成后，公网访问：**`http://<公网IP>:8089`**
