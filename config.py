"""Configuración desde .env local (nunca se sube)."""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
# Canal SOLO para reportes (tasa de acierto + Excel). Si no se define, usa el
# mismo canal principal.
TELEGRAM_REPORTS_CHAT_ID = os.getenv("TELEGRAM_REPORTS_CHAT_ID", "").strip() or TELEGRAM_CHAT_ID
# Usuario dueño: SOLO este id de Telegram puede pedir reportes por comando. Si se
# deja vacío, cualquiera que le escriba al bot podría pedirlos (no recomendado).
TELEGRAM_OWNER_ID = os.getenv("TELEGRAM_OWNER_ID", "").strip()
# Cada cuánto se consulta la lista de próximos partidos.
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
# ¿El Score usa el win% del jugador CON el equipo que va a usar? (prueba A/B).
# Poner SCORE_USE_TEAM=0 en .env para volver al Score anterior (60% carrera/40% H2H).
SCORE_USE_TEAM = os.getenv("SCORE_USE_TEAM", "1").strip().lower() not in ("0", "false", "no", "")
# Umbrales de nivel de confianza (fuerza del favorito). Configurables sin tocar código.
CONF_HIGH = int(os.getenv("CONF_HIGH", "70"))   # ⭐⭐ alta confianza
CONF_MED = int(os.getenv("CONF_MED", "60"))     # ⭐ confianza media
# Enviar tarjeta-IMAGEN (PNG con diseño) en los picks fuertes (ALTA/TOP). Requiere Pillow.
CARD_IMAGE = os.getenv("CARD_IMAGE", "1").strip().lower() not in ("0", "false", "no", "")

_PLACEHOLDERS = {"", "pega_aqui_el_token_del_bot_nuevo", "pega_aqui_el_id_del_canal"}


def setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def validate() -> None:
    faltan = [n for n, v in {"TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
                             "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID}.items()
              if v in _PLACEHOLDERS]
    if faltan:
        print("ERROR: faltan claves en .env: " + ", ".join(faltan), file=sys.stderr)
        raise SystemExit(1)
