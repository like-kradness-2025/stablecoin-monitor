from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stablecoin_monitor.formatting import human_price, human_volume
from stablecoin_monitor.models import FlowSummary


def test_human_price_formats_large_values_with_grouping_and_two_decimals() -> None:
    assert human_price(12345.678) == "$12,345.68"


def test_human_price_formats_sub_10_values_with_six_decimals() -> None:
    assert human_price(1.2345678) == "$1.234568"


def test_human_price_formats_mid_values_with_four_decimals() -> None:
    assert human_price(123.45678) == "$123.4568"


def test_human_price_formats_fractional_values_with_six_decimals() -> None:
    assert human_price(0.12345678) == "$0.123457"


def test_human_volume_formats_suffixes_and_preserves_negative_sign() -> None:
    assert human_volume(1_500_000) == "$1.50M"
    assert human_volume(2_500_000_000) == "$2.50B"
    assert human_volume(3_500_000_000_000) == "$3.50T"
    assert human_volume(-12_345) == "-$12,345"


def test_flow_summary_message_contains_signal_note_and_humanized_values() -> None:
    summary = FlowSummary(
        signal="BTC_DEMAND",
        note="BTC/stable strength detected",
        latest_btc_price=67890.123,
        btc_stable_delta_quote=1_250_000,
        stable_fiat_delta_quote=-500_000,
        stable_cross_delta_quote=0,
    )

    message = summary.to_message()

    assert "BTC $67,890.12" in message
    assert "Signal: BTC_DEMAND" in message
    assert "BTC/stable $1.25M" in message
    assert "stable/fiat -$500,000" in message
    assert "stable/cross $0" in message
    assert message.endswith("BTC/stable strength detected")
