from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import stablecoin_monitor as scm

UTC = timezone.utc


class StablecoinMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix='stablecoin-monitor-test-'))
        self.db_path = self.tmpdir / 'stablecoin_monitor.db'
        self.output_dir = self.tmpdir / 'output'
        self.settings = scm.Settings(
            cmc_api_key='test-key',
            discord_webhook_url=None,
            db_path=self.db_path,
            output_dir=self.output_dir,
            poll_interval_seconds=300,
            history_days=3,
            retention_days=30,
            stable_symbols=('USDT', 'USDC', 'FDUSD'),
            btc_symbol='BTC',
            deviation_alert_bp=15.0,
            request_timeout_seconds=20,
        )
        scm.init_db(self.db_path)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _quotes(self, btc: float, usdt: float, usdc: float, fdusd: float) -> dict[str, dict[str, object]]:
        return {
            'BTC': self._quote('BTC', btc, 1_200_000_000_000, 1),
            'USDT': self._quote('USDT', usdt, 138_000_000_000, 3),
            'USDC': self._quote('USDC', usdc, 53_000_000_000, 7),
            'FDUSD': self._quote('FDUSD', fdusd, 380_000_000, 35),
        }

    def _five_stable_quotes(self) -> dict[str, dict[str, object]]:
        return {
            'BTC': self._quote('BTC', 76000.0, 1_200_000_000_000, 1),
            'USDT': self._quote('USDT', 1.00018, 138_000_000_000, 3),
            'USDC': self._quote('USDC', 0.99982, 53_000_000_000, 7),
            'FDUSD': self._quote('FDUSD', 0.99908, 380_000_000, 35),
            'TUSD': self._quote('TUSD', 1.00002, 280_000_000, 56),
            'DAI': self._quote('DAI', 1.00011, 5_000_000_000, 57),
        }

    def _quote(self, symbol: str, price: float, market_cap: float, cmc_rank: int) -> dict[str, object]:
        return {
            'symbol': symbol,
            'name': symbol,
            'price_usd': price,
            'volume_24h_usd': market_cap * 0.15,
            'market_cap_usd': market_cap,
            'cmc_rank': cmc_rank,
            'last_updated': '2026-04-17T00:00:00Z',
        }

    def _seed_history(self, stable_symbols: tuple[str, ...] | None = None) -> None:
        now = datetime.now(UTC).replace(microsecond=0)
        stable_symbols = stable_symbols or self.settings.stable_symbols
        for idx in range(6):
            ts = now - timedelta(minutes=30 * (6 - idx))
            quotes = {'BTC': self._quote('BTC', 75500 + idx * 120, 1_200_000_000_000, 1)}
            for offset, symbol in enumerate(stable_symbols):
                price = 1.00020 - (offset * 0.00008) + idx * 0.00001
                market_cap = 138_000_000_000 if symbol == 'USDT' else 53_000_000_000 if symbol == 'USDC' else 380_000_000
                if symbol == 'TUSD':
                    market_cap = 280_000_000
                elif symbol == 'DAI':
                    market_cap = 5_000_000_000
                quotes[symbol] = self._quote(symbol, price, market_cap, 3 + offset)
            scm.save_snapshot(self.db_path, ts, quotes)

    def test_choose_best_quote_candidate_prefers_better_rank_then_market_cap(self) -> None:
        items = [
            {'symbol': 'USDT', 'cmc_rank': 20, 'name': 'USDT A', 'quote': {'USD': {'market_cap': 10}}},
            {'symbol': 'USDT', 'cmc_rank': 5, 'name': 'USDT B', 'quote': {'USD': {'market_cap': 1}}},
            {'symbol': 'USDT', 'cmc_rank': 5, 'name': 'USDT C', 'quote': {'USD': {'market_cap': 20}}},
        ]
        best = scm.choose_best_quote_candidate('USDT', items)
        self.assertEqual(best['name'], 'USDT C')

    def test_build_summary_contains_alert_and_volumes(self) -> None:
        self._seed_history()
        quotes = self._quotes(
            btc=76000,
            usdt=1.0018,
            usdc=0.9992,
            fdusd=0.9981,
        )
        history = scm.load_history(self.db_path, self.settings.history_days, (self.settings.btc_symbol, *self.settings.stable_symbols))
        summary = scm.build_summary(self.settings, quotes, history)

        self.assertIn('BTC $76,000.00', summary)
        self.assertIn('USDT $1.001800', summary)
        self.assertIn('ALERT:', summary)
        self.assertIn('FDUSD', summary)

    def test_make_chart_creates_polished_png(self) -> None:
        self._seed_history()
        history = scm.load_history(self.db_path, self.settings.history_days, (self.settings.btc_symbol, *self.settings.stable_symbols))
        chart_path = scm.make_chart(self.settings, history, datetime.now(UTC).replace(microsecond=0))

        self.assertTrue(chart_path.exists())
        self.assertGreater(chart_path.stat().st_size, 10_000)
        self.assertEqual(chart_path.suffix.lower(), '.png')
        with chart_path.open('rb') as fh:
            self.assertEqual(fh.read(8), b'\x89PNG\r\n\x1a\n')

    def test_make_chart_supports_five_stables_without_palette_errors(self) -> None:
        five_settings = scm.Settings(
            cmc_api_key='test-key',
            discord_webhook_url=None,
            db_path=self.db_path,
            output_dir=self.output_dir,
            poll_interval_seconds=300,
            history_days=3,
            retention_days=30,
            stable_symbols=('USDT', 'USDC', 'FDUSD', 'TUSD', 'DAI'),
            btc_symbol='BTC',
            deviation_alert_bp=15.0,
            request_timeout_seconds=20,
        )
        self._seed_history(stable_symbols=five_settings.stable_symbols)
        history = scm.load_history(self.db_path, five_settings.history_days, (five_settings.btc_symbol, *five_settings.stable_symbols))
        chart_path = scm.make_chart(five_settings, history, datetime.now(UTC).replace(microsecond=0))

        self.assertTrue(chart_path.exists())
        self.assertGreater(chart_path.stat().st_size, 10_000)

    def test_run_once_uses_seeded_snapshot_pipeline(self) -> None:
        self._seed_history()
        patched_quotes = self._quotes(
            btc=76123.45,
            usdt=1.0015,
            usdc=0.9996,
            fdusd=0.9989,
        )
        session = scm.build_session()
        try:
            with patch('stablecoin_monitor.fetch_quotes_latest', return_value=patched_quotes), \
                 patch('stablecoin_monitor.send_discord') as send_discord:
                chart_path, summary = scm.run_once(self.settings, session)
        finally:
            session.close()

        self.assertTrue(chart_path.exists())
        self.assertIn('USDT $1.001500', summary)
        self.assertEqual(send_discord.call_count, 0)

    def test_prune_db_removes_old_rows(self) -> None:
        now = datetime.now(UTC).replace(microsecond=0)
        old_quotes = self._quotes(75500, 1.0, 1.0, 1.0)
        new_quotes = self._quotes(75600, 1.0, 1.0, 1.0)
        scm.save_snapshot(self.db_path, now - timedelta(days=40), old_quotes)
        scm.save_snapshot(self.db_path, now, new_quotes)

        deleted = scm.prune_db(self.db_path, retention_days=30)
        self.assertGreater(deleted, 0)
        history = scm.load_history(self.db_path, 365, (self.settings.btc_symbol, *self.settings.stable_symbols))
        self.assertEqual(len(history['BTC']), 1)


if __name__ == '__main__':
    unittest.main()
