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


def _norm(text: str) -> str:
    # "/Semana@EsoccerBot arg" -> "semana"; normaliza acentos (dia/día)
    cmd = text.strip().split()[0].lstrip("/").split("@")[0].lower()
    return cmd.replace("í", "i").replace("á", "a")


def _texts_for(cmd):
    """Devuelve (kind, payload). kind: 'text' | 'excel' | 'help' | 'unknown'."""
    if cmd in ("start", "help", "ayuda"):
        return "help", None
    if cmd in ("hora", "hour"):
        return "text", [reports.text_last_hour()]
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
    return "unknown", None


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
    else:  # text
        for t in payload:
            _send(target_chat, t)


def _handle_channel(chat_id, text):
    """Comando escrito DENTRO de un canal (solo el canal de reportes)."""
    if str(chat_id) != str(config.TELEGRAM_REPORTS_CHAT_ID):
        return  # ignorar comandos en otros canales
    cmd = _norm(text)
    kind, payload = _texts_for(cmd)
    _deliver(config.TELEGRAM_REPORTS_CHAT_ID, kind, payload)  # responde en el mismo canal
    print(f"[CMD] canal cmd={cmd}", file=sys.stderr)


def _handle_private(chat_id, uid, text):
    """Comando por chat privado con el bot (autorizado por id de dueño)."""
    cmd = _norm(text)
    if not _authorized(uid):
        _send(chat_id, "⛔ No autorizado.")
        print(f"[CMD] rechazado uid={uid} cmd={cmd}", file=sys.stderr)
        return
    kind, payload = _texts_for(cmd)
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
                    print(f"[CMD][debug] channel_post chat={cp.get('chat',{}).get('id')} "
                          f"(esperado {config.TELEGRAM_REPORTS_CHAT_ID}) "
                          f"text={text[:40]!r}", file=sys.stderr)
                    if text.startswith("/"):
                        _handle_channel(cp["chat"]["id"], text)
                    continue
                # comando por chat privado con el bot
                msg = upd.get("message")
                if not msg:
                    continue
                text = msg.get("text", "") or ""
                if not text.startswith("/"):
                    continue
                uid = (msg.get("from") or {}).get("id")
                _handle_private(msg["chat"]["id"], uid, text)
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
