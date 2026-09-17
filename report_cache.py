"""Precomputación en segundo plano de los Excel pesados (para respuesta instantánea).

Los reportes que llaman a la API (jugador-equipo, jugador-hora, agenda) tardaban
varios segundos porque se generaban EN VIVO en cada comando. Aquí un hilo los
regenera cada pocos minutos a rutas fijas; el comando solo ENVÍA el archivo ya
listo → respuesta casi instantánea.
"""
import os
import threading
import time

import analytics
import team_report
import agenda
from esb_source import ESBSource

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cache")
REFRESH_SECS = 420  # 7 min

FILES = {
    "equipo": os.path.join(_DIR, "jugador-equipo.csv"),
    "hora": os.path.join(_DIR, "jugador-hora.csv"),
    "agenda": os.path.join(_DIR, "agenda.csv"),
}
_meta = {}  # key -> {"rows": n, "ts": epoch}


def _refresh_once():
    os.makedirs(_DIR, exist_ok=True)
    source = ESBSource()
    try:
        players = team_report.alerted_players()
        agg = team_report.build(source, players, days=3)
        _meta["equipo"] = {"rows": team_report.write_csv(agg, FILES["equipo"]),
                           "ts": time.time()}
    except Exception as e:
        print(f"[WARN] cache equipo: {e}")
    try:
        rows = analytics.player_hour_rows(analytics.load_views())
        _meta["hora"] = {"rows": team_report.write_hours_csv(rows, FILES["hora"]),
                         "ts": time.time()}
    except Exception as e:
        print(f"[WARN] cache hora: {e}")
    try:
        _meta["agenda"] = {"rows": agenda.build_csv(FILES["agenda"], hours=24),
                           "ts": time.time()}
    except Exception as e:
        print(f"[WARN] cache agenda: {e}")
    try:
        import groups
        groups.compute(source)   # deja el snapshot listo para /grupos y la alerta
    except Exception as e:
        print(f"[WARN] cache grupos: {e}")


def _loop():
    while True:
        try:
            _refresh_once()
        except Exception as e:
            print(f"[WARN] report_cache loop: {e}")
        time.sleep(REFRESH_SECS)


def start():
    threading.Thread(target=_loop, daemon=True).start()
    print("[CACHE] precómputo de Excel activo")


def path(key):
    p = FILES.get(key)
    return p if (p and os.path.exists(p)) else None


def age_min(key):
    m = _meta.get(key)
    return int((time.time() - m["ts"]) / 60) if m else None


def rows(key):
    m = _meta.get(key)
    return m["rows"] if m else None
