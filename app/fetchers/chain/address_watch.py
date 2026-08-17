from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import requests

from app.models.entities import OnchainEvent
from app.services.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

SCAN_APIS = {
    "ethereum": "https://api.etherscan.io/api",
    "bsc": "https://api.bscscan.com/api",
}


class AddressWatch:
    def __init__(
        self,
        wallets_path: str | Path,
        cex_wallets_path: str | Path,
        api_keys: dict[str, str],
        min_transfer_usd: float = 10_000,
        rate_limiter: RateLimiter | None = None,
    ):
        self.wallets_path = Path(wallets_path)
        self.cex_wallets_path = Path(cex_wallets_path)
        self.api_keys = api_keys
        self.min_transfer_usd = min_transfer_usd
        self.rate_limiter = rate_limiter or RateLimiter(0.25)
        self.session = requests.Session()
        self._cex_addresses: dict[str, set[str]] = {}
        self._wallets: list[dict[str, Any]] = []
        self._reload_config()

    def _reload_config(self) -> None:
        self._wallets = []
        if self.wallets_path.exists():
            data = json.loads(self.wallets_path.read_text(encoding="utf-8"))
            self._wallets = data.get("wallets", [])

        self._cex_addresses = {}
        if self.cex_wallets_path.exists():
            data = json.loads(self.cex_wallets_path.read_text(encoding="utf-8"))
            for chain, entries in data.items():
                self._cex_addresses[chain] = {
                    e["address"].lower() for e in entries if e.get("address")
                }

    def scan(self) -> list[OnchainEvent]:
        self._reload_config()
        events: list[OnchainEvent] = []
        for wallet in self._wallets:
            chain = wallet.get("chain", "ethereum")
            address = wallet.get("address", "").lower()
            if not address or address.startswith("0x000000"):
                continue
            api_key = self.api_keys.get(chain) or self.api_keys.get(f"{chain}scan_api_key")
            if not api_key:
                continue
            txs = self._fetch_token_txs(chain, address, api_key)
            for tx in txs:
                event = self._parse_tx(wallet, chain, tx)
                if event:
                    events.append(event)
        return events

    def _fetch_token_txs(self, chain: str, address: str, api_key: str) -> list[dict[str, Any]]:
        base = SCAN_APIS.get(chain)
        if not base:
            return []
        try:
            self.rate_limiter.wait()
            resp = self.session.get(
                base,
                params={
                    "module": "account",
                    "action": "tokentx",
                    "address": address,
                    "startblock": 0,
                    "endblock": 99999999,
                    "sort": "desc",
                    "page": 1,
                    "offset": 20,
                    "apikey": api_key,
                },
                timeout=30,
            )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("status") != "1":
                return []
            return payload.get("result", [])
        except Exception as exc:
            logger.warning("Address watch failed %s/%s: %s", chain, address, exc)
            return []

    def _parse_tx(
        self, wallet: dict[str, Any], chain: str, tx: dict[str, Any]
    ) -> OnchainEvent | None:
        from_addr = (tx.get("from") or "").lower()
        to_addr = (tx.get("to") or "").lower()
        watch_addr = wallet.get("address", "").lower()
        if from_addr != watch_addr:
            return None

        token_filter = {t.lower() for t in wallet.get("tokens", []) if t}
        contract = (tx.get("contractAddress") or "").lower()
        if token_filter and contract not in token_filter:
            return None

        decimals = int(tx.get("tokenDecimal") or 18)
        amount = int(tx.get("value") or 0) / (10**decimals)
        ts = int(tx.get("timeStamp") or time.time())
        if ts < time.time() - 86400:
            return None

        cex_set = self._cex_addresses.get(chain, set())
        label = wallet.get("label", "unknown")
        event_type = "LARGE_TRANSFER"
        if to_addr in cex_set:
            event_type = "CEX_DEPOSIT"
        elif label in ("vesting", "unlock"):
            event_type = "UNLOCK_TRANSFER"

        return OnchainEvent(
            ts=ts,
            chain=chain,
            symbol=wallet.get("symbol", "UNKNOWN"),
            event_type=event_type,
            from_address=from_addr,
            to_address=to_addr,
            amount=amount,
            amount_usd=None,
            tx_hash=tx.get("hash", ""),
            label=label,
            raw_json=json.dumps(tx, ensure_ascii=False),
        )
