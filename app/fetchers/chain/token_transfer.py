from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from app.fetchers.chain.rpc_client import RpcClient
from app.models.entities import OnchainEvent, SymbolConfig
from app.models.repositories import Repository

logger = logging.getLogger(__name__)

BLOCKS_PER_DAY = {
    "ethereum": 7200,
    "bsc": 28800,
}


class TokenTransferScanner:
    def __init__(
        self,
        rpc_client: RpcClient,
        repo: Repository,
        cex_wallets_path: str | Path,
        wallets_path: str | Path,
        min_transfer_usd: float = 10_000,
        chunk_size: int = 2000,
    ):
        self.rpc = rpc_client
        self.repo = repo
        self.cex_wallets_path = Path(cex_wallets_path)
        self.wallets_path = Path(wallets_path)
        self.min_transfer_usd = min_transfer_usd
        self.chunk_size = chunk_size

    def scan_symbol(self, symbol_cfg: SymbolConfig, hours: int = 24) -> list[OnchainEvent]:
        if not symbol_cfg.chain or not symbol_cfg.token_contract:
            return []
        chain = symbol_cfg.chain
        contract = symbol_cfg.token_contract.lower()
        latest = self.rpc.get_block_number(chain)
        if not latest:
            return []

        blocks_back = BLOCKS_PER_DAY.get(chain, 7200) * max(1, hours // 24)
        from_block = max(0, latest - blocks_back)
        state_key = f"l2:{chain}:{contract}"
        saved = self.repo.get_scan_state(state_key)
        if saved and saved.isdigit():
            from_block = max(from_block, int(saved))

        cex_set = self._load_cex(chain)
        watch_from = self._load_watch_addresses(chain, symbol_cfg.symbol)

        events: list[OnchainEvent] = []
        cursor = from_block
        while cursor <= latest:
            end = min(cursor + self.chunk_size - 1, latest)
            logs = self.rpc.get_logs(chain, contract, cursor, end)
            for log in logs:
                event = self._parse_log(log, symbol_cfg, chain, cex_set, watch_from)
                if event:
                    events.append(event)
            cursor = end + 1

        self.repo.set_scan_state(state_key, str(latest))
        return events

    def scan_enabled_symbols(
        self, symbols: list[SymbolConfig], hours: int = 24
    ) -> list[OnchainEvent]:
        all_events: list[OnchainEvent] = []
        for sym in symbols:
            if sym.token_contract and sym.chain:
                all_events.extend(self.scan_symbol(sym, hours=hours))
        return all_events

    def _load_cex(self, chain: str) -> set[str]:
        if not self.cex_wallets_path.exists():
            return set()
        data = json.loads(self.cex_wallets_path.read_text(encoding="utf-8"))
        return {e["address"].lower() for e in data.get(chain, []) if e.get("address")}

    def _load_watch_addresses(self, chain: str, symbol: str) -> set[str]:
        if not self.wallets_path.exists():
            return set()
        data = json.loads(self.wallets_path.read_text(encoding="utf-8"))
        result = set()
        for w in data.get("wallets", []):
            if w.get("chain") == chain and w.get("symbol") == symbol:
                result.add(w.get("address", "").lower())
        return result

    def _parse_log(
        self,
        log: dict[str, Any],
        symbol_cfg: SymbolConfig,
        chain: str,
        cex_set: set[str],
        watch_from: set[str],
    ) -> OnchainEvent | None:
        topics = log.get("topics") or []
        if len(topics) < 3:
            return None
        from_addr = "0x" + topics[1][-40:]
        to_addr = "0x" + topics[2][-40:]
        from_addr = from_addr.lower()
        to_addr = to_addr.lower()

        amount_raw = int(log.get("data", "0x0"), 16)
        amount = float(amount_raw) / 1e18

        relevant = to_addr in cex_set or from_addr in watch_from
        if not relevant:
            return None

        event_type = "LARGE_TRANSFER"
        label = "unknown"
        if to_addr in cex_set:
            event_type = "CEX_DEPOSIT"
        if from_addr in watch_from:
            label = "team_or_vesting"

        ts = int(time.time())
        tx_hash = log.get("transactionHash", "")

        return OnchainEvent(
            ts=ts,
            chain=chain,
            symbol=symbol_cfg.base_asset,
            event_type=event_type,
            from_address=from_addr,
            to_address=to_addr,
            amount=amount,
            amount_usd=None,
            tx_hash=tx_hash,
            label=label,
            raw_json=json.dumps(log, ensure_ascii=False),
        )
