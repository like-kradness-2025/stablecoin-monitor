from __future__ import annotations

from pathlib import Path

import requests


def send_discord(webhook_url: str, content: str, chart_path: Path, timeout_seconds: int) -> None:
    with chart_path.open('rb') as fh:
        response = requests.post(
            webhook_url,
            data={'content': content},
            files={'file': (chart_path.name, fh, 'image/png')},
            timeout=timeout_seconds,
        )
    response.raise_for_status()
