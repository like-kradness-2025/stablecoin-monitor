from __future__ import annotations

from datetime import timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
JST = ZoneInfo('Asia/Tokyo')

DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_FETCH_INTERVAL_SECONDS = 60
DEFAULT_RENDER_INTERVAL_SECONDS = 300
DEFAULT_HISTORY_DAYS = 1
DEFAULT_RETENTION_DAYS = 30
DEFAULT_OHLCV_TIMEFRAME = '1m'
DEFAULT_OHLCV_LIMIT = 120
DEFAULT_FLOW_DELTA_THRESHOLD = 1_000_000.0

ALLOWED_CATEGORIES = {'btc_stable', 'stable_fiat', 'stable_cross', 'other'}
