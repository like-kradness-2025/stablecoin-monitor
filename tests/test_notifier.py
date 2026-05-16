from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stablecoin_monitor.notifier import send_discord


class DummyResponse:
    def __init__(self) -> None:
        self.raise_called = False

    def raise_for_status(self) -> None:
        self.raise_called = True


def test_send_discord_posts_content_png_file_and_timeout(tmp_path: Path, monkeypatch) -> None:
    chart_path = tmp_path / "chart.png"
    chart_path.write_bytes(b"png-bytes")
    response = DummyResponse()
    captured: dict[str, object] = {}

    def fake_post(url, *, data, files, timeout):
        captured["url"] = url
        captured["data"] = data
        captured["timeout"] = timeout
        captured["file_name"] = files["file"][0]
        captured["file_mime"] = files["file"][2]
        captured["file_bytes"] = files["file"][1].read()
        return response

    monkeypatch.setattr("stablecoin_monitor.notifier.requests.post", fake_post)

    send_discord("https://example.invalid/webhook", "hello", chart_path, timeout_seconds=12)

    assert captured == {
        "url": "https://example.invalid/webhook",
        "data": {"content": "hello"},
        "timeout": 12,
        "file_name": "chart.png",
        "file_mime": "image/png",
        "file_bytes": b"png-bytes",
    }
    assert response.raise_called is True
