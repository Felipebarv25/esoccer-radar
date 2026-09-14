"""Comandos a pedido por Telegram (chat privado con el bot).

Escucha con getUpdates (long polling) en un hilo daemon y responde reportes al
instante. Los canales NO cambian: esto es para que el DUEÑO le escriba al bot en
privado y pida /semana, /mes, etc. antes de la fecha de corte.

Seguridad: solo responde al TELEGRAM_OWNER_ID (si está configurado).
Nota: un bot solo puede tener UN consumidor de getUpdates a la vez; este servicio
es el único. No usar webhook a la vez.
"""
import concurrent.futures
import sys
import threading
import time
from datetime import datetime, timedelta

import requests

import config
import reports
from telegram_notifier import TelegramNotifier

_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"

# Cada comando se atiende en su propio hilo, para que uno lento (p.ej. /excel)
# no bloquee a los demás (/ayuda y otros reportes responden al instante).
_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="cmd")


def _safe(fn, *args):
    try:
        fn(*args)
    except Exception as e:
        print(f"[WARN] comando: {e}", file=sys.stderr)

_HELP = (
    "🤖 <b>eSoccer Radar — comandos</b>\n\n"
    "/hora — resumen de la última hora completa\n"
    "/horaactual — resumen de la hora en curso\n"
    "/dia — análisis profundo del día (ahora)\n"
    "/semana — tasa de acierto (últimos 7 días)\n"
    "/quincena — tasa (últimos 15 días)\n"
    "/mes — tasa (últimos 30 días)\n"
    "/jugadores [semana|quincena|mes] — mejores jugadores\n"
    "/peores [semana|quincena|mes] — peores jugadores\n"
    "/equipos — mejores/peores duplas jugador+equipo\n"
    "/equipos &lt;jugador&gt; — con qué equipos rinde ese jugador\n"
    "/detalle_jugadores — lista de jugadores (toca uno para su perfil)\n"
    "/&lt;nick&gt; — estadísticas personales de un jugador\n"
    "/calibracion — ¿a más Score/Elo, más acierto?\n"
    "/horas_calientes — franjas donde el favorito gana más/menos\n"
    "/excel — Excel jugador-equipo al momento\n"
    "/dataset — CSV para el modelo (features + resultados)\n"
    "/glosario — qué significa cada dato de la tarjeta\n"
    "/ayuda — esta lista\n\n"
    "📢 Puedes escribir estos comandos <b>directamente en el canal de "
    "reportes</b> y el reporte aparece ahí mismo.\n"
    "<i>Datos observados, NO probabilidad ni rentabilidad.</i>"
)


def _send(chat_id, text):
    try:
        requests.post(_API + "/sendMessage", json={
            "chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True}, timeout=15)
    except Exception as e:
        print(f"[WARN] commands _send: {e}", file=sys.stderr)


def _authorized(uid) -> bool:
    owner = config.TELEGRAM_OWNER_ID
    return (not owner) or (str(uid) == str(owner))


def _norm(tok: str) -> str:
    return tok.lstrip("/").split("@")[0].lower().replace("í", "i").replace("á", "a")


def _split(text: str):
    """Devuelve (cmd, arg, rawarg, raw_cmd). raw_cmd/rawarg conservan mayúsculas (nicks)."""
    t = text.strip()
    parts = t.split()
    cmd = _norm(parts[0]) if parts else ""
    arg = _norm(parts[1]) if len(parts) > 1 else ""
    rawarg = t[len(parts[0]):].strip() if len(parts) > 1 else ""
    raw_cmd = parts[0].lstrip("/").split("@")[0] if parts else ""
    return cmd, arg, rawarg, raw_cmd


def _texts_for(cmd, arg="", rawarg="", raw_cmd=""):
    """Devuelve (kind, payload). kind: 'text' | 'excel' | 'help' | 'unknown'."""
    if cmd in ("start", "help", "ayuda"):
        return "help", None
    if cmd in ("glosario", "diccionario", "info", "como", "significa"):
        return "text", reports.glossary_messages()
    if cmd in ("hora", "hour"):
        return "text", [reports.text_last_hour()]
    if cmd in ("horaactual", "ahora", "actual", "estahora", "horaencurso"):
        return "text", [reports.text_current_hour()]
    if cmd in ("dia", "day"):
        return "text", [reports.text_rate("del día", reports.since_today_utc()),
                        reports.build_daily_deep()]
    if cmd in ("semana", "week"):
        return "text", [reports.text_rate("de la semana", reports.since_days_utc(7))]
    if cmd in ("quincena",):
        return "text", [reports.text_rate("de la quincena", reports.since_days_utc(15))]
    if cmd in ("mes", "month"):
        return "text", [reports.text_rate("del mes", reports.since_days_utc(30))]
    if cmd in ("excel", "csv"):
        return "excel", None
    if cmd in ("dataset", "datos"):
        return "dataset", None
    if cmd in ("calibracion", "calibration", "calib"):
        return "text", [reports.text_calibration()]
    if cmd in ("horas_calientes", "horascalientes", "horas", "hothours"):
        return "text", [reports.text_hot_hours()]
    # listado de jugadores (cada uno como /<nick>)
    if cmd in ("detalle_jugadores", "detallejugadores", "lista_jugadores",
               "jugadores_lista", "lista"):
        page = int(arg) if arg.isdigit() else 1
        import player_profile
        return "text", [player_profile.players_page(page)]
    # perfil explícito: /detalle <nick>
    if cmd in ("detalle", "perfil"):
        if rawarg:
            return "profile", rawarg
        return "text", ["Escribe /detalle <jugador>, o /detalle_jugadores para la lista."]
    # jugador+equipo: /equipos (global) o /equipos <nick> (desglose del jugador)
    if cmd in ("equipos", "combos", "je", "jugadorequipo"):
        if rawarg:
            return "text", [reports.text_player_breakdown(rawarg)]
        return "text", [reports.text_combos()]
    # ranking de jugadores por periodo (mejores o peores)
    fn = periodo = None
    if cmd in ("jugadores", "mejores", "players"):
        fn, periodo = reports.text_top_players, (arg or "dia")
    elif cmd in ("jugadoresdia", "jugadoreshoy"):
        fn, periodo = reports.text_top_players, "dia"
    elif cmd in ("jugadoressemana",):
        fn, periodo = reports.text_top_players, "semana"
    elif cmd in ("jugadoresquincena",):
        fn, periodo = reports.text_top_players, "quincena"
    elif cmd in ("jugadoresmes",):
        fn, periodo = reports.text_top_players, "mes"
    elif cmd in ("peores", "worst"):
        fn, periodo = reports.text_worst_players, (arg or "dia")
    elif cmd in ("peoresdia", "peoreshoy"):
        fn, periodo = reports.text_worst_players, "dia"
    elif cmd in ("peoressemana",):
        fn, periodo = reports.text_worst_players, "semana"
    elif cmd in ("peoresquincena",):
        fn, periodo = reports.text_worst_players, "quincena"
    elif cmd in ("peoresmes",):
        fn, periodo = reports.text_worst_players, "mes"
    else:
        # ¿es el nick de un jugador? (al tocar /DEKSON en la lista)
        import player_profile
        if player_profile.resolve(raw_cmd):
            return "profile", raw_cmd
        return "unknown", None
    return "text", [_ranking(fn, periodo)]


# (label, since_utc, min_n) por periodo — mín. muestra más alto a mayor ventana
_PERIODOS = {
    "dia": ("del día", lambda: reports.since_today_utc(), 2),
    "day": ("del día", lambda: reports.since_today_utc(), 2),
    "semana": ("de la semana", lambda: reports.since_days_utc(7), 3),
    "week": ("de la semana", lambda: reports.since_days_utc(7), 3),
    "quincena": ("de la quincena", lambda: reports.since_days_utc(15), 3),
    "mes": ("del mes", lambda: reports.since_days_utc(30), 4),
    "month": ("del mes", lambda: reports.since_days_utc(30), 4),
}


def _ranking(fn, periodo):
    label, since_fn, min_n = _PERIODOS.get(periodo, _PERIODOS["dia"])
    return fn(label, since_fn(), min_n=min_n)


def _deliver(target_chat, kind, payload):
    """Entrega la respuesta en target_chat (un canal o un chat privado)."""
    if kind == "help":
        _send(target_chat, _HELP)
    elif kind == "unknown":
        _send(target_chat, "❓ Comando no reconocido.\n\n" + _HELP)
    elif kind == "excel":
        _send(target_chat, "📁 Generando Excel jugador-equipo, dame unos segundos...")
        try:
            import team_report
            team_report.generate_and_send(TelegramNotifier(chat_id=target_chat))
        except Exception as e:
            _send(target_chat, "No pude generar el Excel ahora, intenta luego.")
            print(f"[WARN] /excel: {e}", file=sys.stderr)
    elif kind == "dataset":
        _send(target_chat, "🧠 Generando el dataset del modelo, dame unos segundos...")
        try:
            import export_dataset
            export_dataset.generate_and_send(TelegramNotifier(chat_id=target_chat))
        except Exception as e:
            _send(target_chat, "No pude generar el dataset ahora, intenta luego.")
            print(f"[WARN] /dataset: {e}", file=sys.stderr)
    elif kind == "profile":
        _send(target_chat, f"🔎 Buscando estadísticas de {payload}...")
        try:
            import player_profile
            player_profile.generate_and_send(TelegramNotifier(chat_id=target_chat), payload)
        except Exception as e:
            _send(target_chat, "No pude armar el perfil ahora, intenta luego.")
            print(f"[WARN] /perfil {payload}: {e}", file=sys.stderr)
    else:  # text
        for t in payload:
            _send(target_chat, t)


def _handle_channel(chat_id, text):
    """Comando escrito DENTRO de un canal (solo el canal de reportes)."""
    if str(chat_id) != str(config.TELEGRAM_REPORTS_CHAT_ID):
        return  # ignorar comandos en otros canales
    cmd, arg, rawarg, raw_cmd = _split(text)
    kind, payload = _texts_for(cmd, arg, rawarg, raw_cmd)
    _deliver(config.TELEGRAM_REPORTS_CHAT_ID, kind, payload)  # responde en el mismo canal
    print(f"[CMD] canal cmd={cmd} arg={arg}", file=sys.stderr)


def _handle_private(chat_id, uid, text):
    """Comando por chat privado con el bot (autorizado por id de dueño)."""
    cmd, arg, rawarg, raw_cmd = _split(text)
    if not _authorized(uid):
        _send(chat_id, "⛔ No autorizado.")
        print(f"[CMD] rechazado uid={uid} cmd={cmd}", file=sys.stderr)
        return
    kind, payload = _texts_for(cmd, arg, rawarg, raw_cmd)
    if kind in ("help", "unknown"):
        _deliver(chat_id, kind, payload)               # ayuda/errores al privado
    else:
        _deliver(config.TELEGRAM_REPORTS_CHAT_ID, kind, payload)  # reporte al canal
        _send(chat_id, "✅ Enviado al canal de reportes.")
    print(f"[CMD] priv uid={uid} cmd={cmd}", file=sys.stderr)


def poll_loop():
    """Long polling de getUpdates: comandos por chat privado Y dentro del canal."""
    offset = None
    while True:
        try:
            r = requests.get(_API + "/getUpdates", params={
                "offset": offset, "timeout": 50,
                "allowed_updates": '["message","channel_post"]'}, timeout=60)
            data = r.json()
            if not data.get("ok"):
                # 409 = otro getUpdates/webhook activo; esperar y seguir
                print(f"[WARN] getUpdates: {str(data)[:200]}", file=sys.stderr)
                time.sleep(5)
                continue
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                # comando dentro de un canal (el bot es admin del de reportes)
                cp = upd.get("channel_post")
                if cp:
                    text = cp.get("text", "") or ""
                    if text.startswith("/"):
                        _POOL.submit(_safe, _handle_channel, cp["chat"]["id"], text)
                    continue
                # comando por chat privado con el bot
                msg = upd.get("message")
                if not msg:
                    continue
                text = msg.get("text", "") or ""
                if not text.startswith("/"):
                    continue
                uid = (msg.get("from") or {}).get("id")
                _POOL.submit(_safe, _handle_private, msg["chat"]["id"], uid, text)
        except requests.exceptions.RequestException:
            time.sleep(3)  # timeouts del long poll son normales
        except Exception as e:
            print(f"[WARN] commands poll: {e}", file=sys.stderr)
            time.sleep(5)


def start_in_thread():
    if not config.TELEGRAM_OWNER_ID:
        print("[CMD] TELEGRAM_OWNER_ID vacío — los comandos responderían a "
              "CUALQUIERA. Configúralo en .env.", file=sys.stderr)
    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()
    print("[CMD] escucha de comandos activa (getUpdates)", file=sys.stderr)
    return t
