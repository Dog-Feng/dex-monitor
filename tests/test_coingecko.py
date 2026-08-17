def test_pick_bsc_chain_first():
    from unittest.mock import MagicMock, patch

    from app.fetchers.coingecko import CoinGeckoClient

    client = CoinGeckoClient(chain_priority=["binance-smart-chain", "ethereum"])
    with patch.object(client, "search_by_symbol") as search, patch.object(
        client, "get_coin"
    ) as get_coin:
        search.return_value = MagicMock(
            coingecko_id="test", name="Test", symbol="TST", market_cap_rank=500
        )
        get_coin.return_value = {
            "name": "Test",
            "platforms": {
                "ethereum": "0xeth",
                "binance-smart-chain": "0xbsc",
            },
        }
        resolved = client.resolve_from_symbol("TST")
        assert resolved is not None
        assert resolved.chain == "bsc"
        assert resolved.token_contract == "0xbsc"


def test_token_metadata_persisted():
    import sqlite3
    import time

    from app.models.entities import TokenMetadata
    from app.models.repositories import Repository
    from app.models.sqlite import init_db

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    repo = Repository(conn)

    meta = TokenMetadata(
        base_asset="CAKE",
        symbol="CAKEUSDT",
        coingecko_id="pancakeswap-token",
        chain="bsc",
        platform="binance-smart-chain",
        token_contract="0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82",
        name="PancakeSwap",
        market_cap_rank=100,
        resolved_at=int(time.time()),
        updated_at=int(time.time()),
    )
    repo.save_token_metadata(meta)
    loaded = repo.load_token_metadata("CAKE")
    assert loaded is not None
    assert loaded.chain == "bsc"
    assert loaded.token_contract == meta.token_contract
