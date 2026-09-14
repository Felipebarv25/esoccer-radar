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

import analytics

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
    # Denominador HONESTO: TODOS los partidos con favorito que cerraron.
    # El empate cuenta como NO-acierto (el favorito no ganó).
    conpick = st["aciertos"] + st["fallos"] + st["empates"]
    if conpick == 0:
        cuerpo = "Sin partidos cerrados aún en este periodo."
    else:
        rate = round(100 * st["aciertos"] / conpick)
        cuerpo = (f"✅ {st['aciertos']} aciertos · ❌ {st['fallos']} fallos · "
                  f"➖ {st['empates']} empates\n"
                  f"🎯 <b>Tasa de acierto: {rate}%</b> "
                  f"(aciertos sobre {conpick} partidos; el empate NO es acierto)")
    return (f"📊 <b>Reporte {label}</b>\n{cuerpo}\n"
            f"<i>Mide que el favorito GANE, NO rentabilidad ni probabilidad. "
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


def send_startup(notifier) -> None:
    """Reporte de arranque: confirma que el canal de reportes quedó conectado.

    Se envía UNA vez cada vez que el bot arranca. Muestra la tasa de acierto
    acumulada (histórica) para verificar de inmediato el canal, sin esperar a
    la hora programada.
    """
    since = datetime(1970, 1, 1, tzinfo=timezone.utc)
    st = hit_stats(since)
    ahora = datetime.now(_COL).strftime("%Y-%m-%d %H:%M")
    encabezado = (f"🟢 <b>Canal de reportes conectado</b>\n"
                  f"<i>Arranque {ahora} (hora Colombia)</i>\n"
                  f"Aquí llegarán: tasa de acierto (diaria/semanal/quincenal/"
                  f"mensual) y el Excel jugador-equipo.\n\n")
    notifier.send(encabezado + _format("acumulado", st))


def maybe_send(notifier) -> None:
    """Revisa la hora Colombia y envía los reportes que toquen (una vez cada uno)."""
    now = datetime.now(_COL)
    st = _load_state()
    day = now.strftime("%Y-%m-%d")
    week = now.strftime("%Y-W%W")
    month = now.strftime("%Y-%m")
    sent = False

    # Diario 18:00 — tasa del día + análisis profundo
    if now.hour >= 18 and st.get("daily") != day:
        since = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        notifier.send(_format("del día", hit_stats(since)))
        try:
            _send_daily_deep(notifier)
        except Exception as e:
            import sys
            print(f"[WARN] daily_deep: {e}", file=sys.stderr)
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


# ==================== reportes analíticos ====================
_NOTA = ("<i>Datos observados de lo ya jugado — NO probabilidad ni rentabilidad. "
         "Con muestras chicas no es concluyente.</i>")


def _fmt_scoreboard(sb: dict) -> str:
    if sb["analizados"] == 0:
        return "Sin partidos analizados en esta franja."
    linea1 = (f"Partidos analizados: <b>{sb['analizados']}</b>\n"
              f"✅ {sb['acierto']} aciertos · ❌ {sb['fallo']} fallos · "
              f"➖ {sb['empate']} empates · ⚪ {sb['parejo']} parejos")
    if sb["pendiente"]:
        linea1 += f" · ⏳ {sb['pendiente']} sin cerrar"
    partes = [linea1]
    if sb["conpick"]:
        partes.append(f"🎯 <b>Tasa estricta: {sb['tasa_estricta']}%</b> "
                      f"(favorito ganó; empate = no-acierto, sobre {sb['conpick']})")
    if sb["decididos"]:
        partes.append(f"   Tasa entre decididos: {sb['tasa_decididos']}% "
                      f"(sobre {sb['decididos']} con ganador)")
    return "\n".join(partes)


def _fmt_players(rows, titulo, top=5) -> str:
    if not rows:
        return ""
    lineas = [f"🏅 <b>{titulo}</b>"]
    for r in rows[:top]:
        gpg = f" · {r['gpg']} GF/p" if r.get("gpg") is not None else ""
        lineas.append(f"• {r['name']} — {r['wr']}% ({r['w']}/{r['n']}){gpg}")
    return "\n".join(lineas)


def _fmt_teams(rows, titulo, top=5) -> str:
    if not rows:
        return ""
    lineas = [f"🛡️ <b>{titulo}</b>"]
    for r in rows[:top]:
        lineas.append(f"• {r['name']} — {r['wr']}% ({r['w']}/{r['n']})")
    return "\n".join(lineas)


def _fmt_player_team(rows, top=6) -> str:
    if not rows:
        return ""
    lineas = ["🎮 <b>Jugador + equipo destacados</b>"]
    for r in rows[:top]:
        lineas.append(f"• {r['player']} con {r['team']} — {r['wr']}% ({r['w']}/{r['n']})")
    return "\n".join(lineas)


def _fmt_goals(g) -> str:
    if not g:
        return ""
    return (f"⚽ <b>Goles (realizado, {g['n']} part.)</b>\n"
            f"Promedio {g['avg']}/partido · Over 2.5: {g['over25']}% · "
            f"Over 3.5: {g['over35']}% · Ambos anotan: {g['btts']}%")


def _fmt_calibration(rows) -> str:
    if not rows:
        return ""
    lineas = ["📐 <b>Calibración del score</b> (¿a más fuerza, más acierto?)"]
    for r in rows:
        lineas.append(f"• Score {r['banda']} → acierta {r['tasa']}% (n={r['n']})")
    lineas.append("<i>Si la tasa sube con la banda, el score aporta señal.</i>")
    return "\n".join(lineas)


def _fmt_weekday(rows) -> str:
    if not rows:
        return ""
    lineas = ["📅 <b>Por día de la semana</b> (exploratorio)"]
    for r in rows:
        lineas.append(f"• {r['dia']}: {r['tasa']}% (n={r['n']})")
    return "\n".join(lineas)


def _fmt_hourband(rows, top=6) -> str:
    if not rows:
        return ""
    lineas = ["🕐 <b>Franjas horarias más certeras</b> (exploratorio)"]
    for r in rows[:top]:
        lineas.append(f"• {r['franja']}: {r['tasa']}% (n={r['n']})")
    return "\n".join(lineas)


def _join(bloques) -> str:
    return "\n\n".join(b for b in bloques if b)


def build_hourly(win_start, win_end, titulo=None):
    """Texto del resumen de una hora [win_start, win_end) COL. None si 0 partidos."""
    views = analytics.load_views()
    ventana = analytics.in_window(views, win_start, win_end)
    sb = analytics.scoreboard(ventana)
    if sb["analizados"] == 0:
        return None
    cab = titulo or (f"🕐 <b>Reporte {win_start:%H:%M}–{win_end:%H:%M}</b> "
                     f"(hora Colombia, {win_start:%d/%m})")
    return _join([
        cab,
        _fmt_scoreboard(sb),
        _fmt_players(analytics.player_perf(ventana, min_n=1),
                     "Mejores jugadores de la hora", top=5),
        _fmt_goals(analytics.goals_summary(ventana)),
        _NOTA,
    ])


def maybe_hourly(notifier) -> None:
    """Cada hora (a los ~12 min) envía el resumen de la hora COMPLETA anterior.

    Se espera al minuto 12 para que los partidos de esa hora ya hayan cerrado
    (backtest cierra ~12 min tras el inicio). Ventana por hora de INICIO.
    """
    now = datetime.now(_COL)
    if now.minute < 12:
        return
    win_end = now.replace(minute=0, second=0, microsecond=0)
    win_start = win_end - timedelta(hours=1)
    label = win_start.strftime("%Y-%m-%d-%H")
    st = _load_state()
    if st.get("hourly") == label:
        return
    texto = build_hourly(win_start, win_end)
    if texto:  # None = no hubo partidos en esa hora; no mandamos ruido
        notifier.send(texto)
    st["hourly"] = label
    _save_state(st)


def build_daily_deep() -> str:
    """Texto del análisis profundo del día (jugadores, equipos, calibración...)."""
    now = datetime.now(_COL)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    views = analytics.load_views()
    hoy = analytics.in_window(views, day_start, now + timedelta(minutes=1))

    cab = f"🔎 <b>Análisis profundo del día</b> — {now:%d/%m/%Y}"
    return _join([
        cab,
        # del día
        _fmt_players(analytics.player_perf(hoy, min_n=2), "Mejores jugadores del día"),
        _fmt_teams(analytics.team_perf(hoy, min_n=2), "Mejores equipos del día"),
        _fmt_player_team(analytics.player_team(hoy, min_n=2)),
        _fmt_goals(analytics.goals_summary(hoy)),
        # histórico (más muestra) — señal y tendencias
        _fmt_calibration(analytics.score_calibration(views)),
        _fmt_weekday(analytics.weekday_board(views)),
        _fmt_hourband(analytics.hour_board(views, min_n=3)),
        _NOTA,
    ])


def _send_daily_deep(notifier) -> None:
    notifier.send(build_daily_deep())


# ---- generadores "a pedido" (para los comandos de Telegram) ----
def since_today_utc():
    return datetime.now(_COL).replace(hour=0, minute=0, second=0,
                                      microsecond=0).astimezone(timezone.utc)


def since_days_utc(n: int):
    return (datetime.now(_COL) - timedelta(days=n)).astimezone(timezone.utc)


def text_rate(label: str, since_utc) -> str:
    """Tasa de acierto formateada para un periodo (para responder a un comando)."""
    return _format(label, hit_stats(since_utc))


def text_last_hour() -> str:
    """Resumen de la última hora COMPLETA (para el comando /hora)."""
    now = datetime.now(_COL)
    win_end = now.replace(minute=0, second=0, microsecond=0)
    win_start = win_end - timedelta(hours=1)
    return build_hourly(win_start, win_end) or (
        f"🕐 Sin partidos con marcador entre {win_start:%H:%M} y {win_end:%H:%M} COL.")


def text_current_hour() -> str:
    """Resumen de la hora EN CURSO (de la hora en punto hasta ahora). /horaactual."""
    now = datetime.now(_COL)
    win_start = now.replace(minute=0, second=0, microsecond=0)
    titulo = (f"🕐 <b>Hora en curso {win_start:%H:%M}–{now:%H:%M}</b> "
              f"(hora Colombia, {now:%d/%m})")
    txt = build_hourly(win_start, now + timedelta(minutes=1), titulo=titulo)
    return txt or (f"🕐 Aún no hay partidos analizados en la hora en curso "
                   f"({win_start:%H:%M}–{now:%H:%M} COL). Ojo: muchos siguen "
                   f"abiertos — cada partido se cierra ~12 min tras empezar.")


def text_top_players(label: str, since_utc, min_n: int = 2, top: int = 10) -> str:
    """Mejores jugadores (por win% real) en un periodo. Para /jugadores."""
    since_col = since_utc.astimezone(_COL)
    views = analytics.load_views()
    win = analytics.in_window(views, since_col, datetime.now(_COL) + timedelta(minutes=1))
    rows = analytics.player_perf(win, min_n=min_n)
    bloque = _fmt_players(rows, f"Mejores jugadores {label}", top=top)
    if not bloque:
        bloque = (f"🏅 <b>Mejores jugadores {label}</b>\n"
                  f"Sin datos suficientes aún (mínimo {min_n} partidos por jugador).")
    return _join([bloque, _NOTA])


def glossary_messages():
    """Diccionario de datos: qué significa cada dato de la tarjeta. Para /glosario."""
    m1 = (
        "📖 <b>¿Cómo funciona el bot? — Diccionario de datos</b>\n\n"
        "Analiza cada partido de eSports Battle ANTES de que empiece con datos "
        "reales de la API oficial. <b>Es una herramienta de información, NO un "
        "pronóstico ni una probabilidad — no le gana a la casa.</b>\n\n"
        "🧮 <b>Score /100 (Favorito)</b>\n"
        "Resume hacia quién se inclina el partido. <b>50 = parejo</b>, más alto = "
        "más se inclina a ese jugador. Se calcula así:\n"
        "• 40% → diferencia de <b>win% de carrera</b>.\n"
        "• 30% → quién <b>domina el head-to-head</b> (H2H) directo.\n"
        "• 30% → <b>win% con el equipo</b> que cada uno va a usar (si hay muestra "
        "suficiente; si no, vuelve a 60% carrera / 40% H2H).\n"
        "Mostramos el número del <b>favorito</b> (el más fuerte). "
        "<i>NO es probabilidad de ganar: es fuerza relativa según su historial.</i>\n\n"
        "🔢 <b>Elo</b>\n"
        "Número de fuerza de cada jugador (todos arrancan en 1500). Sube al ganar "
        "y baja al perder, y se mueve <b>más</b> cuando el resultado es sorpresa "
        "(el débil le gana al fuerte). La diferencia de Elo se traduce en una "
        "probabilidad: «Elo A 1600 vs B 1500 → esperado A 64%». Va al lado del "
        "Score para comparar cuál acierta más. Es auto-corrector y viene sembrado "
        "con miles de partidos. <i>Fuerza observada, no adivinación.</i>\n\n"
        "🚨 <b>ON FIRE</b>\n"
        "Aparece solo si, con NUESTROS datos acumulados, un jugador trae ventaja "
        "clara: rinde con ese equipo, domina a ese rival, va fuerte en esa hora, "
        "o viene en racha. Solo salta con muestra suficiente.\n\n"
        "👤 <b>Carrera win%</b>\n"
        "Porcentaje de partidos ganados en TODA la carrera de cada jugador.\n\n"
        "📈 <b>Forma reciente (gol/partido)</b>\n"
        "En sus últimos torneos: goles a favor ⚽ y en contra 🥅 por partido. "
        "Mide su momento actual (ataque/defensa)."
    )
    m2 = (
        "⚔️ <b>H2H (head-to-head)</b>\n"
        "Historial DIRECTO entre esos dos jugadores. «H2H (8): A 5-1-2 B» = de 8 "
        "duelos, A ganó 5, empataron 1, B ganó 2.\n\n"
        "⚽ <b>Goles H2H</b>\n"
        "Sobre esos duelos directos:\n"
        "• <b>prom</b> = goles totales promedio por partido.\n"
        "• <b>O2.5 / O3.5 / O4.5</b> (Over) = % de partidos con MÁS de 2.5 / 3.5 / "
        "4.5 goles (o sea 3+, 4+, 5+ goles en total).\n"
        "• <b>BTTS</b> (both teams to score) = % de partidos en que <b>ambos</b> "
        "anotaron.\n\n"
        "📋 <b>Últimos</b>\n"
        "Los marcadores más recientes entre ambos.\n\n"
        "🔎 <b>Confiabilidad</b>\n"
        "🟢 muestra amplia · 🟡 media · 🔴 pequeña (tómalo con pinzas). Mientras "
        "menos partidos haya, menos confiable es el análisis.\n\n"
        "🏁 <b>Cierre del partido</b> (respuesta ✅/❌ bajo la alerta)\n"
        "• ✅ <b>Acertó</b>: el favorito ganó.\n"
        "• ❌ <b>Falló</b>: el favorito no ganó.\n"
        "• ➖ <b>Empate</b>: terminó igualado.\n"
        "• ⚪ <b>Parejo</b>: no había favorito (score 50).\n\n"
        "<i>Todo son datos observados, no garantías. Apuesta solo lo que puedas "
        "perder.</i>"
    )
    return [m1, m2]


def text_combos(min_n: int = 2, top: int = 12) -> str:
    """Ranking global de duplas jugador+equipo (mejores y peores). Para /equipos."""
    views = analytics.load_views()
    rows = analytics.player_team(views, min_n=min_n)  # ya viene ordenado desc por wr
    if not rows:
        return _join(["🎮 <b>Duplas jugador+equipo</b>\n"
                      f"Sin datos suficientes aún (mínimo {min_n} partidos por dupla).",
                      _NOTA])

    def _line(r):
        return f"• {r['player']} con {r['team']} — {r['wr']}% ({r['w']}/{r['n']})"

    if len(rows) <= top:
        lineas = ["🎮 <b>Duplas jugador+equipo</b> (mejor → peor)"]
        lineas += [_line(r) for r in rows]
    else:
        peores = sorted(rows, key=lambda r: (r["wr"], -r["n"]))[:top]
        lineas = ["🔝 <b>Mejores duplas jugador+equipo</b>"]
        lineas += [_line(r) for r in rows[:top]]
        lineas += ["", "📉 <b>Peores duplas jugador+equipo</b>"]
        lineas += [_line(r) for r in peores]
    return _join(["\n".join(lineas), _NOTA])


def text_player_breakdown(nick: str) -> str:
    """Desglose de un jugador equipo por equipo (con cuáles va bien/mal). /equipos <nick>."""
    views = analytics.load_views()
    nombres = analytics.player_names(views)
    match = next((n for n in nombres if n.lower() == nick.lower()), None)
    if not match:
        cands = sorted(n for n in nombres if nick.lower() in n.lower())
        if len(cands) == 1:
            match = cands[0]
        elif cands:
            return "🔎 ¿A cuál te refieres? " + ", ".join(cands[:12])
        else:
            return f"No tengo datos de «{nick}» todavía."
    rows = analytics.player_team_breakdown(views, match, min_n=1)
    if not rows:
        return f"Sin partidos cerrados de {match} aún."
    lineas = [f"🎮 <b>{match} — rendimiento por equipo</b>"]
    for r in rows:
        if r["n"] < 3:
            flag = "⚠️"
        elif r["wr"] >= 60:
            flag = "✅"
        elif r["wr"] <= 40:
            flag = "🔻"
        else:
            flag = "•"
        lineas.append(f"{flag} {r['team']} — {r['wr']}% ({r['w']}/{r['n']})")
    lineas.append("<i>✅ juega bien · 🔻 juega mal · ⚠️ muestra chica (&lt;3)</i>")
    return _join(["\n".join(lineas), _NOTA])


def text_worst_players(label: str, since_utc, min_n: int = 2, top: int = 10) -> str:
    """Peores jugadores (menor win% real) en un periodo. Para /peores."""
    since_col = since_utc.astimezone(_COL)
    views = analytics.load_views()
    win = analytics.in_window(views, since_col, datetime.now(_COL) + timedelta(minutes=1))
    rows = analytics.player_perf(win, min_n=min_n)
    # peor primero: menor win%; a igualdad, muestra más grande (más confiable)
    rows = sorted(rows, key=lambda r: (r["wr"], -r["n"]))
    if not rows:
        return _join([f"📉 <b>Peores jugadores {label}</b>\n"
                      f"Sin datos suficientes aún (mínimo {min_n} partidos por jugador).",
                      _NOTA])
    lineas = [f"📉 <b>Peores jugadores {label}</b>"]
    for r in rows[:top]:
        gpg = f" · {r['gpg']} GF/p" if r.get("gpg") is not None else ""
        lineas.append(f"• {r['name']} — {r['wr']}% ({r['w']}/{r['n']}){gpg}")
    return _join(["\n".join(lineas), _NOTA])


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
