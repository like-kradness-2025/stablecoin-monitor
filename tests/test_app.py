from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stablecoin_monitor.app import StablecoinMonitorApp
from stablecoin_monitor.models import MarketConfig, Settings


def _settings(tmp_path: Path, webhook_url: str | None = None) -> Settings:
    return Settings(
        discord_webhook_url=webhook_url,
        db_path=tmp_path / "monitor.sqlite3",
        output_dir=tmp_path / "output",
        poll_interval_seconds=300,
        fetch_interval_seconds=60,
        render_interval_seconds=300,
        history_days=1,
        retention_days=30,
        request_timeout_seconds=20,
        markets_config_path=tmp_path / "markets.yaml",
        ohlcv_timeframe="1m",
        ohlcv_limit=120,
        flow_delta_threshold_quote=100.0,
    )


def _market() -> MarketConfig:
    return MarketConfig(
        market_key="binance:btc/usdt",
        exchange="binance",
        symbol="BTC/USDT",
        base_symbol="BTC",
        quote_symbol="USDT",
        category="btc_stable",
        priority=1,
        enabled=True,
    )


class DummyFetcher:
    def __init__(self, bars):
        self.bars = bars
        self.called_with = None

    def fetch(self, markets):
        self.called_with = markets
        return self.bars


class DummySummary:
    def to_message(self) -> str:
        return "summary-message"


def test_fetch_and_store_snapshot_returns_true_when_bars_are_saved(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    markets = [_market()]
    bars = [object()]
    app = StablecoinMonitorApp(settings, markets)
    dummy_fetcher = DummyFetcher(bars)
    cast(Any, app).fetcher = dummy_fetcher
    captured: dict[str, object] = {}

    def fake_save(db_path, saved_bars):
        captured["db_path"] = db_path
        captured["bars"] = saved_bars

    monkeypatch.setattr("stablecoin_monitor.app.save_ohlcv_bars", fake_save)
    monkeypatch.setattr("stablecoin_monitor.app.prune_db", lambda db_path, retention_days: 0)

    assert app.fetch_and_store_snapshot() is True
    assert captured == {"db_path": settings.db_path, "bars": bars}
    assert dummy_fetcher.called_with == markets


def test_fetch_and_store_snapshot_returns_false_when_no_bars(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    app = StablecoinMonitorApp(settings, [_market()])
    cast(Any, app).fetcher = DummyFetcher([])
    monkeypatch.setattr("stablecoin_monitor.app.save_ohlcv_bars", lambda db_path, bars: None)
    monkeypatch.setattr("stablecoin_monitor.app.prune_db", lambda db_path, retention_days: 0)

    assert app.fetch_and_store_snapshot() is False


def test_render_and_notify_chart_skips_when_history_is_empty(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    app = StablecoinMonitorApp(settings, [_market()])
    monkeypatch.setattr("stablecoin_monitor.app.load_ohlcv_history", lambda db_path, history_days: {})

    assert app.render_and_notify_chart() is False


def test_render_and_notify_chart_sends_discord_when_webhook_is_configured(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path, webhook_url="https://example.invalid/webhook")
    markets = [_market()]
    app = StablecoinMonitorApp(settings, markets)
    history = {"binance:btc/usdt": [{"bucket_start_utc": object(), "proxy_cvd_quote": 1.0, "close": 100.0}]}
    chart_path = tmp_path / "chart.png"
    captured: dict[str, object] = {}

    monkeypatch.setattr("stablecoin_monitor.app.load_ohlcv_history", lambda db_path, history_days: history)
    monkeypatch.setattr("stablecoin_monitor.app.make_chart", lambda settings_arg, markets_arg, history_arg, ts_utc: chart_path)
    monkeypatch.setattr("stablecoin_monitor.app.classify_flow", lambda settings_arg, markets_arg, history_arg: DummySummary())

    def fake_send(webhook_url, content, path, timeout_seconds):
        captured["webhook_url"] = webhook_url
        captured["content"] = content
        captured["path"] = path
        captured["timeout_seconds"] = timeout_seconds

    monkeypatch.setattr("stablecoin_monitor.app.send_discord", fake_send)

    assert app.render_and_notify_chart() is True
    assert captured == {
        "webhook_url": settings.discord_webhook_url,
        "content": "summary-message",
        "path": chart_path,
        "timeout_seconds": settings.request_timeout_seconds,
    }


def test_run_once_stops_before_render_when_fetch_fails(tmp_path: Path, monkeypatch) -> None:
    app = StablecoinMonitorApp(_settings(tmp_path), [_market()])
    monkeypatch.setattr(app, "fetch_and_store_snapshot", lambda: False)

    def fail_render():
        raise AssertionError("render should not be called")

    monkeypatch.setattr(app, "render_and_notify_chart", fail_render)

    assert app.run_once() is False
