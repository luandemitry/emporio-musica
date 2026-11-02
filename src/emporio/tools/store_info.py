"""Is the store open right now? (manual, section 2)

Hours: Mon-Fri 09:00-18:00, Sat 09:00-13:00, closed on Sundays and holidays.
Holidays covered: national fixed dates, Good Friday, and the Campo Grande / MS dates.
Anything else (e.g. a one-off municipal holiday) is not known; the result says so.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from ..clock import Clock

WEEKDAYS_PT = [
    "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
    "sexta-feira", "sábado", "domingo",
]  # fmt: skip

OPENING_HOURS: dict[int, tuple[time, time]] = {
    **{wd: (time(9), time(18)) for wd in range(5)},  # Monday-Friday
    5: (time(9), time(13)),  # Saturday
}  # Sunday (6) is closed

FIXED_HOLIDAYS = {
    (1, 1): "Confraternização Universal",
    (4, 21): "Tiradentes",
    (5, 1): "Dia do Trabalho",
    (8, 26): "Aniversário de Campo Grande",
    (9, 7): "Independência do Brasil",
    (10, 11): "Criação do Estado de Mato Grosso do Sul",
    (10, 12): "Nossa Senhora Aparecida",
    (11, 2): "Finados",
    (11, 15): "Proclamação da República",
    (11, 20): "Consciência Negra",
    (12, 25): "Natal",
}


def easter(year: int) -> date:
    """Gregorian Easter (anonymous algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def holiday_name(day: date) -> str | None:
    if day == easter(day.year) - timedelta(days=2):
        return "Sexta-feira Santa"
    return FIXED_HOLIDAYS.get((day.month, day.day))


def _closed_reason(day: date) -> str | None:
    if (name := holiday_name(day)) is not None:
        return f"feriado ({name})"
    if day.weekday() not in OPENING_HOURS:
        return "domingo"
    return None


def _label(day: date, at: time | None = None) -> str:
    text = f"{WEEKDAYS_PT[day.weekday()]}, {day:%d/%m/%Y}"
    return text + (f" às {at:%H:%M}" if at else "")


def _next_opening(now: datetime) -> tuple[date, time]:
    for offset in range(0, 15):
        day = now.date() + timedelta(days=offset)
        if _closed_reason(day):
            continue
        opens, _ = OPENING_HOURS[day.weekday()]
        if offset > 0 or now.time() < opens:
            return day, opens
    raise RuntimeError("no opening day found")  # unreachable: holidays are never 14 days in a row


def store_status(clock: Clock) -> dict:
    """Open/closed right now, today's hours and the next opening."""
    now = clock.now()
    today = now.date()
    reason = _closed_reason(today)
    hours = None if reason else OPENING_HOURS[today.weekday()]

    result: dict = {
        "now": f"{_label(today)}, {now:%H:%M}",
        "today_hours": f"{hours[0]:%H:%M} às {hours[1]:%H:%M}" if hours else "fechado",
        "weekly_hours": {
            "segunda a sexta-feira": "09:00 às 18:00",
            "sábado": "09:00 às 13:00",
            "domingo e feriados": "fechado",
        },
        "note": (
            "Special dates (Black Friday, Aniversário da Loja, Natal) may have extended hours, "
            "and local holidays beyond the ones known here are not covered."
        ),
    }
    if hours and hours[0] <= now.time() < hours[1]:
        result.update(is_open=True, closes_at=f"{hours[1]:%H:%M}")
    else:
        result["is_open"] = False
        result["closed_reason"] = reason or "fora do horário de funcionamento"
        next_day, opens = _next_opening(now)
        result["next_opening"] = _label(next_day, opens)
    return result
