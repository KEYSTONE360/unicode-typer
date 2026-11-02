"""Validate decimal timing values without silently rounding user input."""
from decimal import Decimal, InvalidOperation

MIN_INTERVAL_MS = Decimal('0.01')
MAX_INTERVAL_MS = Decimal('5000')

def parse_interval_ms(value):
    try:
        interval = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError('글자 간격은 숫자로 입력하십시오.') from exc
    if not interval.is_finite() or not MIN_INTERVAL_MS <= interval <= MAX_INTERVAL_MS:
        raise ValueError('글자 간격은 0.01~5000ms 범위로 입력하십시오.')
    return float(interval)

def parse_delay_seconds(value):
    try:
        delay = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError('시작 대기는 숫자로 입력하십시오.') from exc
    if not delay.is_finite() or not Decimal('1') <= delay <= Decimal('60'):
        raise ValueError('시작 대기는 1~60초 범위로 입력하십시오.')
    return float(delay)
