"""Comandos a pedido por Telegram (chat privado con el bot).

Escucha con getUpdates (long polling) en un hilo daemon y responde reportes al
instante. Los canales NO cambian: esto es para que el DUEÑO le escriba al bot en
privado y pida /semana, /mes, etc. antes de la fecha de corte.

Seguridad: solo responde al TELEGRAM_OWNER_ID (si está configurado).
Nota: un bot solo puede tener UN consumidor de getUpdates a la vez; este servicio
es el único. No usar webhook a la vez.
"""
import sys
import threading
import time
from datetime import datetime, timedelta

import requests

import config
import reports
from telegram_notifier import TelegramNotifier

_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"

_HELP = (
    "🤖 <b>eSoccer Radar — comandos</b>\n\n"
    "/hora — resumen de la última hora\n"
    "/dia — análisis profundo del día (ahora)\n"
    "/semana — tasa de acierto (últimos 7 días)\n"
    "/quincena — tasa (últimos 15 días)\n"
    "/mes — tasa (últimos 30 días)\n"
    "/excel — Excel jugador-equipo al momento\n"
    "/ayuda — esta lista\n\n"
    "📢 El reporte se publica en el <b>canal de reportes</b>.\n"
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


def _post(requester_chat, *textos):
    """Publica el/los reporte(s) en el canal de reportes y avisa al que pidió."""
    for t in textos:
        _send(config.TELEGRAM_REPORTS_CHAT_ID, t)
    _send(requester_chat, "✅ Enviado al canal de reportes.")


def _handle(chat_id, uid, text):
    # normaliza: "/Semana@EsoccerBot argumento" -> "semana"
    cmd = text.strip().split()[0].lstrip("/").split("@")[0].lower()
    cmd = (cmd.replace("í", "i").replace("á", "a"))  # dia/día, etc.

    if not _authorized(uid):
        _send(chat_id, "⛔ No autorizado.")
        print(f"[CMD] rechazado uid={uid} cmd={cmd}", file=sys.stderr)
        return

    if cmd in ("start", "help", "ayuda"):
        _send(chat_id, _HELP)
    elif cmd in ("hora", "hour"):
        _post(chat_id, reports.text_last_hour())
    elif cmd in ("dia", "day"):
        _post(chat_id,
              reports.text_rate("del día", reports.since_today_utc()),
              reports.build_daily_deep())
    elif cmd in ("semana", "week"):
        _post(chat_id, reports.text_rate("de la semana", reports.since_days_utc(7)))
    elif cmd in ("quincena",):
        _post(chat_id, reports.text_rate("de la quincena", reports.since_days_utc(15)))
    elif cmd in ("mes", "month"):
        _post(chat_id, reports.text_rate("del mes", reports.since_days_utc(30)))
    elif cmd in ("excel", "csv"):
        _send(chat_id, "📁 Generando Excel jugador-equipo, dame unos segundos...")
        try:
            import team_report
            team_report.generate_and_send(
                TelegramNotifier(chat_id=config.TELEGRAM_REPORTS_CHAT_ID))
            _send(chat_id, "✅ Excel enviado al canal de reportes.")
        except Exception as e:
            _send(chat_id, "No pude generar el Excel ahora, intenta luego.")
            print(f"[WARN] /excel: {e}", file=sys.stderr)
    else:
        _send(chat_id, "❓ Comando no reconocido.\n\n" + _HELP)

    print(f"[CMD] uid={uid} cmd={cmd}", file=sys.stderr)


def poll_loop():
    """Long polling de getUpdates. Solo mensajes privados/grupo (no channel_post)."""
    offset = None
    while True:
        try:
            r = requests.get(_API + "/getUpdates", params={
                "offset": offset, "timeout": 50,
                "allowed_updates": '["message"]'}, timeout=60)
            data = r.json()
            if not data.get("ok"):
                # 409 = otro getUpdates/webhook activo; esperar y seguir
                print(f"[WARN] getUpdates: {str(data)[:200]}", file=sys.stderr)
                time.sleep(5)
                continue
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message")
                if not msg:
                    continue
                text = msg.get("text", "") or ""
                if not text.startswith("/"):
                    continue
                chat_id = msg["chat"]["id"]
                uid = (msg.get("from") or {}).get("id")
                _handle(chat_id, uid, text)
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
