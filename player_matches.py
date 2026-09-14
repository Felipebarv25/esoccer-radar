"""Excel de TODOS los partidos de un jugador (comando /<nick>_Excel).

Recorre los torneos jugados del jugador, saca cada partido con marcador, y
exporta un CSV fácil de leer: fecha, tipo, resultado, equipo usado, rival,
equipo rival, goles. Ordenado del más reciente al más antiguo.

Es INFO observada de lo ya jugado, no un pronóstico.
"""
import concurrent.futures
import csv
import os
import time
from datetime import datetime, timezone, timedelta

import requests

from esb_source import ESBSource, _PLAYED_TOURNAMENTS, match_type

_COL = timezone(timedelta(hours=-5))
_OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "reports")


def _get(url, headers, params=None, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=20)
            if r.status_code == 429:
                time.sleep(1 + i)
                continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(0.5 + i)


def collect(nick, source: ESBSource = None, max_tournaments=150, workers=8):
    """Todos los partidos con marcador del jugador (hasta max_tournaments torneos)."""
    source = source or ESBSource()
    headers = dict(source.session.headers)

    # 1) torneos jugados del jugador
    tid_type = {}
    try:
        first = source.participant_tournaments(nick, page=1)
        total = first.get("totalPages", 1)
        for pg in range(1, total + 1):
            data = first if pg == 1 else source.participant_tournaments(nick, page=pg)
            for t in (data.get("tournaments") or []):
                if t.get("status_id") in _PLAYED_TOURNAMENTS:
                    tid_type[t["id"]] = match_type(t.get("token_international"))
            if len(tid_type) >= max_tournaments or pg >= min(total, 20):
                break
    except Exception:
        pass

    # 2) partidos de esos torneos (en paralelo)
    def fetch(tid):
        return _get(f"{source.base}/tournaments/{tid}/matches", headers)

    low = nick.lower()
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch, tid): (tid, typ) for tid, typ in tid_type.items()}
        for fut in concurrent.futures.as_completed(futs):
            typ = futs[fut][1]
            try:
                data = fut.result()
            except Exception:
                continue
            arr = data if isinstance(data, list) else (data.get("matches") or [])
            for m in arr:
                p1, p2 = m.get("participant1", {}), m.get("participant2", {})
                s1, s2 = p1.get("score"), p2.get("score")
                if s1 is None or s2 is None:
                    continue
                if (p1.get("nickname") or "").lower() == low:
                    me, opp, sm, so = p1, p2, s1, s2
                elif (p2.get("nickname") or "").lower() == low:
                    me, opp, sm, so = p2, p1, s2, s1
                else:
                    continue
                try:
                    dt = datetime.fromisoformat((m.get("date") or "").replace("Z", "+00:00"))
                    fecha = dt.astimezone(_COL).strftime("%Y-%m-%d %H:%M")
                    orden = dt
                except Exception:
                    fecha, orden = "", datetime.min.replace(tzinfo=timezone.utc)
                res = "✅ Ganó" if sm > so else ("➖ Empató" if sm == so else "❌ Perdió")
                rows.append({
                    "_orden": orden, "fecha_COL": fecha, "tipo": typ,
                    "resultado": res,
                    "equipo_usado": (me.get("team") or {}).get("token_international"),
                    "rival": opp.get("nickname"),
                    "equipo_rival": (opp.get("team") or {}).get("token_international"),
                    "goles_favor": sm, "goles_contra": so,
                    "marcador": f"{sm}-{so}",
                })
    rows.sort(key=lambda r: r["_orden"], reverse=True)
    return rows


def build_csv(nick, path, source=None):
    rows = collect(nick, source=source)
    fields = ["fecha_COL", "tipo", "resultado", "equipo_usado", "rival",
              "equipo_rival", "goles_favor", "goles_contra", "marcador"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return rows


def generate_and_send(notifier, canon_nick):
    rows = collect(canon_nick)
    if not rows:
        notifier.send(f"No encontré partidos con marcador de {canon_nick}.")
        return 0
    g = sum(1 for r in rows if r["resultado"].startswith("✅"))
    e = sum(1 for r in rows if r["resultado"].startswith("➖"))
    pdd = sum(1 for r in rows if r["resultado"].startswith("❌"))
    fecha = datetime.now(_COL).strftime("%Y-%m-%d")
    path = os.path.join(_OUT_DIR, f"{canon_nick}_partidos_{fecha}.csv")
    fields = ["fecha_COL", "tipo", "resultado", "equipo_usado", "rival",
              "equipo_rival", "goles_favor", "goles_contra", "marcador"]
    os.makedirs(_OUT_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    wr = round(100 * g / len(rows)) if rows else 0
    caption = (f"📄 <b>{canon_nick} — todos sus partidos</b>\n"
               f"{len(rows)} partidos · ✅ {g} ➖ {e} ❌ {pdd} · win {wr}%\n"
               f"Cada fila: fecha, tipo, resultado, equipo usado, rival, goles. "
               f"Ábrelo en Excel y ordena/filtra como quieras.\n"
               f"<i>Historial reciente (hasta ~150 torneos). Datos observados.</i>")
    notifier.send_document(path, caption)
    print(f"[REPORT] partidos de {canon_nick} enviados ({len(rows)})")
    return len(rows)
