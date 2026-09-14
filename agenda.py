"""Agenda de próximos partidos (hoy + mañana) en Excel, ordenada por probabilidad.

Trae los partidos PROGRAMADOS de las próximas horas desde la API, estima la
probabilidad del favorito con el Elo (rápido, sin llenar la API), marca si el
favorito usa un equipo del TOP 50, y exporta un CSV fácil de leer ordenado de
mayor a menor probabilidad.

HONESTO: la probabilidad es la del Elo (fuerza relativa), no una garantía.
"""
import concurrent.futures
import csv
import os
import time
from datetime import datetime, timedelta, timezone

import requests

import elo
import team_ratings
from esb_source import ESBSource, _FINISHED_TOURNAMENT, match_type

_COL = timezone(timedelta(hours=-5))
_OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "reports")
_SCHEDULED = 1  # status_id de partido programado


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


def collect(source: ESBSource, hours: int = 24, workers: int = 6):
    """Partidos PROGRAMADOS en las próximas `hours` (jugadores, equipos, tipo, fecha)."""
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=hours)
    df = now.strftime("%Y/%m/%d %H:%M")
    dt = cutoff.strftime("%Y/%m/%d %H:%M")
    headers = dict(source.session.headers)
    turl = f"{source.base}/tournaments"

    first = _get(turl, headers, {"page": 1, "dateFrom": df, "dateTo": dt})
    total = min(first.get("totalPages", 1), 80)
    pages = [first]
    if total > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_get, turl, headers, {"page": p, "dateFrom": df, "dateTo": dt})
                    for p in range(2, total + 1)]
            for fut in concurrent.futures.as_completed(futs):
                try:
                    pages.append(fut.result())
                except Exception:
                    pass

    # torneos NO terminados (los que aún pueden tener partidos programados)
    tid_type = {t["id"]: match_type(t.get("token_international"))
                for pg in pages for t in pg.get("tournaments", [])
                if t.get("status_id") != _FINISHED_TOURNAMENT}

    def fetch(tid):
        return _get(f"{source.base}/tournaments/{tid}/matches", headers)

    def in_window(iso):
        try:
            md = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
        except Exception:
            return False
        return now <= md <= cutoff

    matches = []
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
                if m.get("status_id") != _SCHEDULED or not in_window(m.get("date")):
                    continue
                p1, p2 = m.get("participant1", {}), m.get("participant2", {})
                n1, n2 = p1.get("nickname"), p2.get("nickname")
                if not n1 or not n2:
                    continue
                matches.append({
                    "date": m.get("date"), "type": typ,
                    "p1": n1, "t1": (p1.get("team") or {}).get("token_international"),
                    "p2": n2, "t2": (p2.get("team") or {}).get("token_international"),
                })
    return matches


def _rows(matches, top_set):
    out = []
    for m in matches:
        e1, _ = elo.get(m["p1"])
        e2, _ = elo.get(m["p2"])
        exp1 = elo.expected(e1, e2)
        if exp1 >= 0.5:
            fav, favteam, oppn, oppteam, prob = m["p1"], m["t1"], m["p2"], m["t2"], exp1
            elo_fav, elo_opp = e1, e2
        else:
            fav, favteam, oppn, oppteam, prob = m["p2"], m["t2"], m["p1"], m["t1"], 1 - exp1
            elo_fav, elo_opp = e2, e1
        g, w, wr = team_ratings.get(fav, favteam)
        es_top = "⭐ TOP" if (fav, favteam) in top_set else ""
        try:
            hora = datetime.fromisoformat((m["date"] or "").replace("Z", "+00:00")).astimezone(_COL)
            hora_txt = hora.strftime("%d/%m %H:%M")
        except Exception:
            hora_txt = ""
        out.append({
            "hora_COL": hora_txt, "tipo": m["type"],
            "prob_%": round(100 * prob), "TOP": es_top,
            "favorito": fav, "equipo_favorito": favteam,
            "winrate_fav_con_equipo": (round(100 * wr) if wr is not None else ""),
            "rival": oppn, "equipo_rival": oppteam,
            "elo_favorito": round(elo_fav), "elo_rival": round(elo_opp),
        })
    out.sort(key=lambda r: r["prob_%"], reverse=True)
    return out


def build_csv(path: str, hours: int = 24) -> int:
    source = ESBSource()
    matches = collect(source, hours=hours)
    top_set = {(r["player"], r["team"]) for r in team_ratings.top(min_games=10, limit=50)}
    rows = _rows(matches, top_set)
    fields = ["hora_COL", "tipo", "prob_%", "TOP", "favorito", "equipo_favorito",
              "winrate_fav_con_equipo", "rival", "equipo_rival",
              "elo_favorito", "elo_rival"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def generate_and_send(notifier, hours: int = 24) -> int:
    fecha = datetime.now(_COL).strftime("%Y-%m-%d")
    path = os.path.join(_OUT_DIR, f"agenda_{fecha}.csv")
    n = build_csv(path, hours=hours)
    if n == 0:
        notifier.send("📅 No hay partidos programados en la ventana consultada "
                      "(la API aún no publica los de más adelante).")
        return 0
    caption = (f"📅 <b>Agenda — próximas {hours}h</b>\n"
               f"{n} partidos programados, ordenados de MAYOR a menor probabilidad "
               f"(según Elo). La columna <b>⭐ TOP</b> marca cuando el favorito usa "
               f"un equipo del top 50. Ábrelo en Excel.\n"
               f"<i>Probabilidad = fuerza relativa del Elo, NO garantía.</i>")
    notifier.send_document(path, caption)
    print(f"[REPORT] agenda enviada ({n} partidos)")
    return n
