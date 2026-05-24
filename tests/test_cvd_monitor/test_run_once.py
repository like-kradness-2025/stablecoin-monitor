from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cvd_monitor import cli
from cvd_monitor.market_registry import resolve_markets_by_symbol
from cvd_monitor.models import MarketConfig, RunOnceResult


def _market(market_key: str, coinalyze_symbol: str, symbol: str, symbol_on_exchange: str) -> MarketConfig:
    base_symbol, quote_symbol = symbol.split('/', 1)
    return MarketConfig(
        market_key=market_key,
        exchange=market_key.split(':', 1)[0],
        coinalyze_symbol=coinalyze_symbol,
        symbol=symbol,
        symbol_on_exchange=symbol_on_exchange,
        display_pair=symbol,
        market_type='spot',
        base_symbol=base_symbol,
        quote_symbol=quote_symbol,
        category='btc_stable',
        priority=1,
        enabled=True,
    )


def test_parse_args_includes_run_once_command(monkeypatch) -> None:
    monkeypatch.setattr(sys, 'argv', ['cvd_monitor', 'run-once', '--output', 'out.png'])
    args = cli.parse_args()
    assert args.command == 'run-once'
    assert args.output == 'out.png'


def test_run_once_result_schema() -> None:
    result = RunOnceResult(True, 10, 20, 20, 18, 10, 2, 2, 1, 0, ['BTCUSD.A'], 'out.png', True, False, None, 'done')
    assert result.success is True
    assert result.received_rows == 10
    assert result.computed_rows == 20
    assert result.selected_markets_count == 20
    assert result.markets_with_feature_rows_count == 18
    assert result.failed_symbols == ['BTCUSD.A']
    assert result.rendered_path == 'out.png'
    assert result.discord_enabled is True
    assert result.discord_sent is False
    assert result.discord_error is None
    assert result.summary == 'done'


def test_main_run_once_smoke(monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def fake_run_once_pipeline(**kwargs):
        assert kwargs['symbols'] == ['BTCUSD.A', 'FDUSDUSD.A']
        assert kwargs['output'] == tmp_path / 'chart.png'
        return RunOnceResult(True, 0, 0, 0, 0, 0, 0, 0, 0, 0, [], str(tmp_path / 'chart.png'), False, False, None, 'ok')

    monkeypatch.setattr(cli, 'run_once_pipeline', fake_run_once_pipeline)
    monkeypatch.setattr(cli, 'parse_args', lambda: type('Args', (), {
        'command': 'run-once',
        'universe': None,
        'interval': '5min',
        'lookback_hours': 6,
        'symbols': 'BTCUSD.A,FDUSDUSD.A',
        'limit': 2,
        'output': str(tmp_path / 'chart.png'),
        'discord': True,
        'dry_run': False,
        'rolling_hours': 24,
        'log_level': 'INFO',
    })())
    monkeypatch.setattr(cli, 'configure_logging', lambda level: None)

    assert cli.main() == 0
    assert capsys.readouterr().out.strip() == 'ok'


def test_run_once_secret_leak_guard(monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    secret = 'https://discord.example.invalid/webhook/SECRET-TOKEN'

    def fake_run_once_pipeline(**kwargs):
        assert kwargs['discord'] is True
        return RunOnceResult(True, 0, 0, 0, 0, 0, 0, 0, 0, 0, [], str(tmp_path / 'chart.png'), False, False, None, f'summary ok for {kwargs["interval"]}')

    monkeypatch.setattr(cli, 'run_once_pipeline', fake_run_once_pipeline)
    monkeypatch.setattr(cli, 'parse_args', lambda: type('Args', (), {
        'command': 'run-once',
        'universe': None,
        'interval': '5min',
        'lookback_hours': 6,
        'symbols': 'BTCUSD.A',
        'limit': None,
        'output': str(tmp_path / 'chart.png'),
        'discord': True,
        'dry_run': False,
        'rolling_hours': 24,
        'log_level': 'INFO',
    })())
    monkeypatch.setattr(cli, 'configure_logging', lambda level: None)
    monkeypatch.setenv('DISCORD_WEBHOOK_URL', secret)

    assert cli.main() == 0
    output = capsys.readouterr().out
    assert secret not in output


def test_run_once_market_key_identity_and_duplicate_guards() -> None:
    markets = [
        _market('binance:BTCUSD.A', 'BTCUSD.A', 'BTC/USD', 'BTCUSDT'),
        _market('binance:FDUSDUSD.A', 'FDUSDUSD.A', 'FDUSD/USD', 'FDUSDUSDT'),
    ]
    selected = resolve_markets_by_symbol(markets, ['BTCUSD.A', 'BTC/USD', 'FDUSDUSD.A', 'FDUSDUSDT'])
    assert [m.market_key for m in selected] == ['binance:BTCUSD.A', 'binance:FDUSDUSD.A']
