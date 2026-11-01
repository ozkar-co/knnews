"""Efemerides astronomicas diarias para el feed de ciencia.

Calcula fase lunar, salida/puesta de Luna y Sol, duracion del dia, proximos
eventos (luna nueva/llena, equinoccio, solsticio) y la proxima lluvia de
meteoros, para Pereira (Colombia).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    import ephem
except ImportError:  # el servicio sigue funcionando sin efemerides
    ephem = None

MONTHS = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
WEEKDAYS = [
    "lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo",
]
METEOR_SHOWERS = [
    ("Cuadrántidas", 1, 3),
    ("Líridas", 4, 22),
    ("Eta Acuáridas", 5, 6),
    ("Perseidas", 8, 12),
    ("Oriónidas", 10, 21),
    ("Leónidas", 11, 17),
    ("Gemínidas", 12, 14),
    ("Úrsidas", 12, 22),
]


def _local(ephem_date, tz):
    dt = ephem_date.datetime().replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def _hm(dt):
    return dt.strftime("%H:%M")


def _date_str(dt):
    return f"{dt.day} de {MONTHS[dt.month - 1]} de {dt.year}"


def _phase_name(age):
    if age < 1.0 or age >= 28.5:
        return "Luna nueva"
    if age < 5.5:
        return "Luna creciente"
    if age < 9.0:
        return "Cuarto creciente"
    if age < 13.0:
        return "Gibosa creciente"
    if age < 16.5:
        return "Luna llena"
    if age < 20.0:
        return "Gibosa menguante"
    if age < 23.5:
        return "Cuarto menguante"
    return "Luna menguante"


def _next_shower(now):
    candidates = []
    for name, month, day in METEOR_SHOWERS:
        dt = datetime(now.year, month, day, tzinfo=now.tzinfo)
        if dt.date() < now.date():
            dt = datetime(now.year + 1, month, day, tzinfo=now.tzinfo)
        candidates.append((dt, name))
    dt, name = min(candidates, key=lambda item: item[0])
    return f"{name}, {_date_str(dt)}"


def daily_item(settings):
    """Devuelve un item RSS sintetico o None si no hay ephem."""
    if not settings.get("ephemeris", True) or ephem is None:
        return None

    tz = ZoneInfo(settings.get("ephemeris_tz", "America/Bogota"))
    now = datetime.now(tz)
    base = ephem.Date(now.astimezone(timezone.utc).replace(tzinfo=None))

    obs = ephem.Observer()
    obs.lat = str(settings.get("ephemeris_lat", 4.8133))
    obs.lon = str(settings.get("ephemeris_lon", -75.6961))
    obs.elevation = 0
    obs.date = base

    def event(body, rising=True):
        obs.date = base
        try:
            value = obs.next_rising(body) if rising else obs.next_setting(body)
            return _hm(_local(value, tz))
        except Exception:  # noqa: BLE001 - circumpolar o sin evento
            return "—"

    moon = ephem.Moon(obs)
    illumination = moon.phase
    age = float(base - ephem.previous_new_moon(base))
    phase = _phase_name(age)

    sunrise = event(ephem.Sun(), True)
    sunset = event(ephem.Sun(), False)
    moonrise = event(ephem.Moon(), True)
    moonset = event(ephem.Moon(), False)

    day_length = "—"
    try:
        obs.date = base
        rise = _local(obs.next_rising(ephem.Sun()), tz)
        obs.date = base
        setting = _local(obs.next_setting(ephem.Sun()), tz)
        if setting < rise:
            setting += timedelta(days=1)
        minutes = int((setting - rise).total_seconds() // 60)
        day_length = f"{minutes // 60} h {minutes % 60:02d} min"
    except Exception:  # noqa: BLE001
        pass

    next_new = _date_str(_local(ephem.next_new_moon(base), tz))
    next_full = _date_str(_local(ephem.next_full_moon(base), tz))
    next_equinox = _date_str(_local(ephem.next_equinox(base), tz))
    next_solstice = _date_str(_local(ephem.next_solstice(base), tz))
    next_shower = _next_shower(now)

    city = settings.get("ephemeris_city", "Pereira")
    title = (
        f"Efemérides astronómicas · {WEEKDAYS[now.weekday()]}, "
        f"{_date_str(now)}"
    )
    body = (
        f"<p><strong>Fase lunar:</strong> {phase} "
        f"({illumination:.0f}% iluminada)</p>"
        "<ul>"
        f"<li>Salida de la Luna: {moonrise}</li>"
        f"<li>Puesta de la Luna: {moonset}</li>"
        f"<li>Salida del Sol: {sunrise}</li>"
        f"<li>Puesta del Sol: {sunset}</li>"
        f"<li>Duración del día: {day_length}</li>"
        f"<li>Próxima luna nueva: {next_new}</li>"
        f"<li>Próxima luna llena: {next_full}</li>"
        f"<li>Próximo equinoccio: {next_equinox}</li>"
        f"<li>Próximo solsticio: {next_solstice}</li>"
        f"<li>Próxima lluvia de meteoros: {next_shower}</li>"
        "</ul>"
        f"<p><small>Calculado para {city} "
        f"({settings.get('ephemeris_lat')}, {settings.get('ephemeris_lon')}).</small></p>"
    )
    summary = (
        f"{phase} ({illumination:.0f}%). Sol {sunrise}-{sunset}. "
        f"Luna {moonrise}-{moonset}."
    )
    return {
        "id": -1,
        "guid": f"ephemeris-{now.date().isoformat()}",
        "source": "Efemérides",
        "title": title,
        "link": f"{settings.get('base_url', '').rstrip('/')}/feeds/tema-ciencia.xml",
        "summary": summary,
        "content": body,
        "ts": int(now.timestamp()),
        "topic": "ciencia",
        "score": 0.0,
        "importance": 0.0,
        "synthetic": True,
    }
