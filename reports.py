"""Reportes de tasa de acierto (#5b), programados en hora Colombia.

- Diario   18:00 COL  → aciertos del día
- Domingo  12:00 COL  → aciertos de la semana
- Días 1 y 16, 18:00  → aciertos de la quincena
- Día 1,   18:00 COL  → aciertos del mes

La tasa mide si la data ACIERTA al favorito (ganó), NO si es rentable ni una
probabilidad. Con pocos partidos no es concluyente.
"""
import glob
import json
import os
from datetime import datetime, timedelta, timezone

_COL = timezone(timedelta(hours=-5))
_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "matches")
_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports_state.json")


def _closed_records():
    for p in glob.glob(os.path.join(_DIR, "*.json")):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                rec = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        res = rec.get("result")
        if res and res.get("closed_at"):
            yield rec, res


def hit_stats(since_utc: datetime) -> dict:
    """Tasa de acierto sobre partidos cerrados desde `since_utc`."""
    aciertos = fallos = empates = parejos = 0
    for rec, res in _closed_records():
        try:
            closed = datetime.fromisoformat(res["closed_at"])
        except Exception:
            continue
        if closed < since_utc:
            continue
        h = res.get("hit")
        if h is True:
            aciertos += 1
        elif h is False:
            fallos += 1
        elif res.get("outcome") == "empate":
            empates += 1
        elif res.get("outcome") == "parejo":
            parejos += 1
    decididos = aciertos + fallos
    rate = round(100 * aciertos / decididos) if decididos else None
    return {"aciertos": aciertos, "fallos": fallos, "empates": empates,
            "parejos": parejos, "decididos": decididos, "rate": rate}


def _format(label: str, st: dict) -> str:
    if st["decididos"] == 0:
        cuerpo = "Sin partidos cerrados aún en este periodo."
    else:
        cuerpo = (f"✅ {st['aciertos']} aciertos · ❌ {st['fallos']} fallos · "
                  f"➖ {st['empates']} empates\n"
                  f"🎯 <b>Tasa de acierto: {st['rate']}%</b> "
                  f"(sobre {st['decididos']} con favorito)")
    return (f"📊 <b>Reporte {label}</b>\n{cuerpo}\n"
            f"<i>Mide acierto al favorito, NO rentabilidad ni probabilidad. "
            f"Con pocos datos no es concluyente.</i>")


def _load_state() -> dict:
    try:
        with open(_STATE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(st: dict) -> None:
    with open(_STATE, "w", encoding="utf-8") as fh:
        json.dump(st, fh)


def maybe_send(notifier) -> None:
    """Revisa la hora Colombia y envía los reportes que toquen (una vez cada uno)."""
    now = datetime.now(_COL)
    st = _load_state()
    day = now.strftime("%Y-%m-%d")
    week = now.strftime("%Y-W%W")
    month = now.strftime("%Y-%m")
    sent = False

    # Diario 18:00
    if now.hour >= 18 and st.get("daily") != day:
        since = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        notifier.send(_format("del día", hit_stats(since)))
        st["daily"] = day; sent = True

    # Semanal domingo 12:00 (weekday: lunes=0 ... domingo=6)
    if now.weekday() == 6 and now.hour >= 12 and st.get("weekly") != week:
        since = (now - timedelta(days=7)).astimezone(timezone.utc)
        notifier.send(_format("de la semana", hit_stats(since)))
        st["weekly"] = week; sent = True

    # Quincenal días 1 y 16, 18:00
    if now.day in (1, 16) and now.hour >= 18 and st.get("quincenal") != day:
        since = (now - timedelta(days=15)).astimezone(timezone.utc)
        notifier.send(_format("de la quincena", hit_stats(since)))
        st["quincenal"] = day; sent = True

    # Mensual día 1, 18:00
    if now.day == 1 and now.hour >= 18 and st.get("monthly") != month:
        since = (now - timedelta(days=30)).astimezone(timezone.utc)
        notifier.send(_format("del mes", hit_stats(since)))
        st["monthly"] = month; sent = True

    if sent:
        _save_state(st)


def maybe_team_report(notifier) -> None:
    """Cada 2 días, a las 18:00 COL, genera y envía el CSV jugador-equipo (#5c).

    Corre en un hilo aparte porque tarda (recorre muchos torneos).
    """
    import threading

    now = datetime.now(_COL)
    if now.hour < 18:
        return
    st = _load_state()
    day = now.strftime("%Y-%m-%d")
    last = st.get("team_report")
    if last == day:
        return
    fire = True
    if last:
        try:
            d0 = datetime.strptime(last, "%Y-%m-%d").date()
            fire = (now.date() - d0).days >= 2
        except Exception:
            fire = True
    if not fire:
        return
    st["team_report"] = day
    _save_state(st)

    def _run():
        try:
            import team_report
            team_report.generate_and_send(notifier)
        except Exception as e:
            import sys
            print(f"[WARN] team_report: {e}", file=sys.stderr)

    threading.Thread(target=_run, daemon=True).start()
