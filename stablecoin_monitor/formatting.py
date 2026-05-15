from __future__ import annotations


def human_price(value: float) -> str:
    if abs(value) >= 1000:
        return f'${value:,.2f}'
    if abs(value) >= 1:
        return f'${value:,.6f}' if abs(value) < 10 else f'${value:,.4f}'
    return f'${value:.6f}'


def human_volume(value: float) -> str:
    abs_value = abs(value)
    sign = '-' if value < 0 else ''
    abs_value = abs(value)
    if abs_value >= 1_000_000_000_000:
        return f'{sign}${abs_value / 1_000_000_000_000:.2f}T'
    if abs_value >= 1_000_000_000:
        return f'{sign}${abs_value / 1_000_000_000:.2f}B'
    if abs_value >= 1_000_000:
        return f'{sign}${abs_value / 1_000_000:.2f}M'
    return f'{sign}${abs_value:,.0f}'
