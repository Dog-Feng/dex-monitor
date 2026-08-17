from __future__ import annotations

import logging
from typing import Any

import requests

from app.services.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


class RpcClient:
    def __init__(self, rpc_urls: dict[str, str], rate_limiter: RateLimiter | None = None):
        self.rpc_urls = {k: v for k, v in rpc_urls.items() if v and "YOUR_KEY" not in v}
        self.rate_limiter = rate_limiter or RateLimiter(0.2)
        self.session = requests.Session()

    def get_block_number(self, chain: str) -> int | None:
        result = self._call(chain, "eth_blockNumber", [])
        if result is None:
            return None
        return int(result, 16)

    def get_logs(
        self,
        chain: str,
        token_contract: str,
        from_block: int,
        to_block: int,
    ) -> list[dict[str, Any]]:
        params = [
            {
                "fromBlock": hex(from_block),
                "toBlock": hex(to_block),
                "address": token_contract,
                "topics": [TRANSFER_TOPIC],
            }
        ]
        result = self._call(chain, "eth_getLogs", params)
        return result or []

    def _call(self, chain: str, method: str, params: list[Any]) -> Any:
        url = self.rpc_urls.get(chain)
        if not url:
            return None
        try:
            self.rate_limiter.wait()
            resp = self.session.post(
                url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=30,
            )
            resp.raise_for_status()
            payload = resp.json()
            if "error" in payload:
                logger.warning("RPC error on %s: %s", chain, payload["error"])
                return None
            return payload.get("result")
        except Exception as exc:
            logger.warning("RPC call failed (%s.%s): %s", chain, method, exc)
            return None
