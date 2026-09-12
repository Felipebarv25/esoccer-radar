"""Vigilancia de salud (health check) del bot.

Avisa al canal de reportes cuando algo va mal con la fuente de datos:
  - la API lleva un rato dando errores (posible bloqueo/caída), o
  - no llegan partidos nuevos desde hace demasiado (por defecto 3 horas).

Envía UNA sola alarma por incidente (no spamea) y un aviso de recuperación
cuando vuelve la normalidad.
"""
import sys
from datetime import datetime, timedelta, timezone


class Watchdog:
    def __init__(self, notifier, quiet_hours: float = 3, error_minutes: float = 15):
        self.notifier = notifier
        self.quiet = timedelta(hours=quiet_hours)
        self.errwin = timedelta(minutes=error_minutes)
        now = datetime.now(timezone.utc)
        self.last_match = now      # última vez que vimos ≥1 partido
        self.first_error = None    # inicio de la racha de errores actual
        self.alerted_quiet = False
        self.alerted_error = False

    def _send(self, text):
        try:
            self.notifier.send(text)
        except Exception as e:
            print(f"[WARN] watchdog send: {e}", file=sys.stderr)

    def ok_fetch(self, n_matches: int):
        """Llamar cuando la consulta a la API salió bien (sin excepción)."""
        now = datetime.now(timezone.utc)
        # se recuperó de una racha de errores
        if self.alerted_error:
            self._send("✅ <b>API recuperada</b>\nVolvió a responder correctamente.")
        self.first_error = None
        self.alerted_error = False

        if n_matches > 0:
            self.last_match = now
            if self.alerted_quiet:
                self._send("✅ <b>Datos de nuevo</b>\nVolvieron a llegar partidos. "
                           "Todo normal.")
                self.alerted_quiet = False
        else:
            self._check_quiet(now)

    def fetch_error(self):
        """Llamar cuando la consulta a la API lanzó excepción."""
        now = datetime.now(timezone.utc)
        if self.first_error is None:
            self.first_error = now
        if now - self.first_error >= self.errwin and not self.alerted_error:
            mins = int((now - self.first_error).total_seconds() // 60)
            self._send(f"🚨 <b>Alerta: la API no responde</b>\n"
                       f"Lleva ~{mins} min con errores seguidos (posible bloqueo o "
                       f"caída). Revisa el servicio si continúa.")
            self.alerted_error = True
        self._check_quiet(now)

    def _check_quiet(self, now):
        if now - self.last_match >= self.quiet and not self.alerted_quiet:
            horas = (now - self.last_match).total_seconds() / 3600
            self._send(f"🚨 <b>Alerta: sin partidos hace {horas:.1f} h</b>\n"
                       f"No han llegado partidos nuevos en un buen rato. Puede ser "
                       f"un bloqueo de la API, una caída, o una pausa larga de "
                       f"eSports Battle. Revisa si esto no es normal.")
            self.alerted_quiet = True
