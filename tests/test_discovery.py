from unittest.mock import MagicMock

from app.models.entities import SymbolConfig
from app.services.symbol_discovery import SymbolDiscovery


def test_discovery_selects_by_15m_change():
    binance = MagicMock()
    binance.fetch_tickers_24hr.return_value = [
        {
            "symbol": "AAAUSDT",
            "priceChangePercent": "50",
            "quoteVolume": "10000000",
            "lastPrice": "1.0",
        },
        {
            "symbol": "BBBUSDT",
            "priceChangePercent": "5",
            "quoteVolume": "8000000",
            "lastPrice": "2.0",
        },
        {
            "symbol": "CCCUSDT",
            "priceChangePercent": "-30",
            "quoteVolume": "12000000",
            "lastPrice": "0.5",
        },
    ]

    def short_change(symbol: str, bars: int = 3):
        mapping = {"AAAUSDT": 0.12, "BBBUSDT": 0.01, "CCCUSDT": -0.09}
        return mapping.get(symbol)

    binance.fetch_short_term_change.side_effect = short_change
    binance.is_tradfi_perpetual.return_value = False
    binance.refresh_symbol_meta.return_value = None
    binance.refresh_ticker_24h.return_value = {}

    discovery = SymbolDiscovery(
        binance,
        {
            "enabled": True,
            "mode": "dynamic",
            "top_gainers": 2,
            "top_losers": 2,
            "min_quote_volume_usdt": 5_000_000,
            "min_change_15m": 0.03,
            "fallback_top_n": 1,
        },
    )
    result = discovery.resolve([])
    symbols = {s.symbol for s in result}
    assert "AAAUSDT" in symbols
    assert "CCCUSDT" in symbols
    assert "BBBUSDT" not in symbols


def test_discovery_excludes_tradfi_perpetuals():
    binance = MagicMock()
    binance.fetch_tickers_24hr.return_value = [
        {
            "symbol": "AAAUSDT",
            "priceChangePercent": "50",
            "quoteVolume": "10000000",
            "lastPrice": "1.0",
        },
        {
            "symbol": "TSLAUSDT",
            "priceChangePercent": "80",
            "quoteVolume": "20000000",
            "lastPrice": "400",
        },
    ]
    binance.is_tradfi_perpetual.side_effect = lambda s: s == "TSLAUSDT"
    binance.fetch_short_term_change.return_value = 0.12
    binance.refresh_symbol_meta.return_value = None
    binance.refresh_ticker_24h.return_value = {"AAAUSDT": 0.5, "TSLAUSDT": 0.8}

    discovery = SymbolDiscovery(
        binance,
        {
            "enabled": True,
            "mode": "dynamic",
            "top_gainers": 5,
            "top_losers": 5,
            "min_quote_volume_usdt": 5_000_000,
            "min_change_15m": 0.03,
            "exclude_tradfi": True,
        },
    )
    result = discovery.resolve([])
    symbols = {s.symbol for s in result}
    assert "AAAUSDT" in symbols
    assert "TSLAUSDT" not in symbols
    binance.is_tradfi_perpetual.assert_called()


def test_discovery_fixed_top_gainers():
    binance = MagicMock()
    tickers = []
    for i in range(40):
        tickers.append(
            {
                "symbol": f"TOK{i}USDT",
                "priceChangePercent": str(40 - i),
                "quoteVolume": "10000000",
                "lastPrice": "1.0",
            }
        )
    tickers.append(
        {
            "symbol": "TSLAUSDT",
            "priceChangePercent": "99",
            "quoteVolume": "20000000",
            "lastPrice": "400",
        }
    )
    binance.fetch_tickers_24hr.return_value = tickers
    binance.is_tradfi_perpetual.side_effect = lambda s: s == "TSLAUSDT"
    binance.fetch_short_term_change.return_value = 0.01
    binance.refresh_symbol_meta.return_value = None
    binance.refresh_ticker_24h.return_value = {}

    discovery = SymbolDiscovery(
        binance,
        {
            "enabled": True,
            "mode": "dynamic",
            "fixed_top_gainers": 30,
            "min_quote_volume_usdt": 5_000_000,
            "exclude_tradfi": True,
        },
    )
    result = discovery.resolve([])
    assert len(result) == 30
    symbols = {s.symbol for s in result}
    assert "TSLAUSDT" not in symbols
    assert "TOK0USDT" in symbols
    assert "TOK29USDT" in symbols
    assert "TOK30USDT" not in symbols


def test_hybrid_merges_static_chain_info():
    binance = MagicMock()
    binance.fetch_tickers_24hr.return_value = [
        {
            "symbol": "AAAUSDT",
            "priceChangePercent": "20",
            "quoteVolume": "9000000",
            "lastPrice": "1",
        }
    ]
    binance.fetch_short_term_change.return_value = 0.1
    binance.is_tradfi_perpetual.return_value = False
    binance.refresh_symbol_meta.return_value = None
    binance.refresh_ticker_24h.return_value = {"AAAUSDT": 0.2}

    discovery = SymbolDiscovery(
        binance,
        {"enabled": True, "mode": "hybrid", "top_gainers": 5, "top_losers": 5, "min_change_15m": 0.03},
    )
    static = [
        SymbolConfig(
            symbol="AAAUSDT",
            base_asset="AAA",
            chain="ethereum",
            token_contract="0xabc",
            coingecko_id="aaa",
        )
    ]
    merged = discovery.resolve(static)
    aaa = next(s for s in merged if s.symbol == "AAAUSDT")
    assert aaa.token_contract == "0xabc"
    assert aaa.coingecko_id == "aaa"
