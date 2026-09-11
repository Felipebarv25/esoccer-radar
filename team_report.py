"""Reporte por JUGADOR + EQUIPO (#5c): CSV que abre en Excel.

Para ver si un jugador rinde bien con TODOS los equipos o solo con algunos
(la observación "bueno con el City, malo con el Madrid"). Recorre los torneos
TERMINADOS de los últimos días y agrega, por (jugador, equipo): partidos, W/D/L,
% de victoria, goles a favor/contra por partido.

HONESTO: por (jugador, equipo) la muestra suele ser pequeña. Cada fila trae su
número de partidos para que sepas cuánto pesa; pocas partidas = anecdótico.
"""
import concurrent.futures
import csv
import glob
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests

import analytics
from esb_source import ESBSource, _FINISHED_TOURNAMENT

_MATCH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "matches")
_OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "reports")


def alerted_players() -> set:
    """Jugadores que hemos analizado (de los registros guardados)."""
    players = set()
    for p in glob.glob(os.path.join(_MATCH_DIR, "*.json")):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                rec = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        for k in ("player1", "player2"):
            if rec.get(k):
                players.add(rec[k])
    return players


def _results_concurrent(source: ESBSource, tids: list, workers: int = 8) -> dict:
    """Trae /tournaments/{id}/results de varios torneos EN PARALELO.

    Usa la caché del source (evita re-pedir) y peticiones independientes por hilo
    (requests.get es thread-safe; no comparte la Session del source).
    """
    out, need, now = {}, [], time.time()
    for tid in tids:
        hit = source._cache.get(f"tres:{tid}")
        if hit and now - hit[0] < source.cache_ttl:
            out[tid] = hit[1]
        else:
            need.append(tid)

    headers = dict(source.session.headers)

    def fetch(tid):
        r = requests.get(f"{source.base}/tournaments/{tid}/results",
                         headers=headers, timeout=20)
        r.raise_for_status()
        return tid, r.json()

    if need:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            for fut in concurrent.futures.as_completed([ex.submit(fetch, t) for t in need]):
                try:
                    tid, data = fut.result()
                    out[tid] = data
                    source._cache[f"tres:{tid}"] = (time.time(), data)  # calienta caché
                except Exception:
                    pass
    return out


def build(source: ESBSource, players: set, days: int = 3, max_pages: int = 60) -> dict:
    now = datetime.now(timezone.utc)
    df = (now - timedelta(days=days)).strftime("%Y/%m/%d %H:%M")
    dt = now.strftime("%Y/%m/%d %H:%M")

    # 1) recorrer las páginas de torneos y juntar los ids de los TERMINADOS
    tids, page, total = [], 1, 1
    while page <= total and page <= max_pages:
        data = source._get("/tournaments", params={"page": page, "dateFrom": df, "dateTo": dt})
        total = data.get("totalPages", 1)
        for t in data.get("tournaments", []):
            if t.get("status_id") == _FINISHED_TOURNAMENT:
                tids.append(t["id"])
        page += 1

    # 2) traer los resultados de todos esos torneos en paralelo
    results = _results_concurrent(source, tids)

    # 3) agregar por (jugador, equipo)
    agg = defaultdict(lambda: {"games": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0})
    for res in results.values():
        for row in (res.get("results") or []):
            part = row.get("participant") or {}
            nick = part.get("nickname")
            if players and nick not in players:
                continue
            team = (part.get("team") or {}).get("token_international", "?")
            d = row.get("details") or {}
            a = agg[(nick, team)]
            a["games"] += d.get("GP", 0); a["w"] += d.get("W", 0)
            a["d"] += d.get("D", 0); a["l"] += d.get("L", 0)
            a["gf"] += d.get("GF", 0); a["ga"] += d.get("GA", 0)
    return agg


def write_csv(agg: dict, path: str) -> int:
    rows = []
    for (nick, team), a in agg.items():
        g = a["games"]
        if g == 0:
            continue
        rows.append({
            "jugador": nick, "equipo": team, "partidos": g,
            "G": a["w"], "E": a["d"], "P": a["l"],
            "win_%": round(100 * a["w"] / g),
            "goles_a_favor_pp": round(a["gf"] / g, 2),
            "goles_en_contra_pp": round(a["ga"] / g, 2),
            "muestra": "ok" if g >= 6 else "poca",
        })
    rows.sort(key=lambda r: (r["jugador"], -r["partidos"]))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else
                           ["jugador", "equipo", "partidos"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def write_hours_csv(rows: list, path: str) -> int:
    fields = ["jugador", "hora", "partidos", "G", "win_%",
              "goles_a_favor_pp", "goles_en_contra_pp", "muestra"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def generate_hours_and_send(notifier) -> int:
    """Genera y envía el CSV jugador-hora (en qué franjas juega cada uno)."""
    rows = analytics.player_hour_rows(analytics.load_views())
    if not rows:
        notifier.send("🕐 Aún no hay datos de jugador–hora (se llena con los "
                      "cierres de partidos). Prueba de nuevo más tarde.")
        return 0
    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(_OUT_DIR, f"jugador-hora_{fecha}.csv")
    n = write_hours_csv(rows, path)
    caption = (f"🕐 <b>Reporte jugador–hora</b>\n"
               f"{n} combinaciones jugador+franja horaria (hora Colombia). "
               f"Sirve para ver en qué horas suele jugar cada uno y en cuáles "
               f"le va mejor. Ábrelo en Excel y ordena por jugador o por win_%.\n"
               f"<i>Muestra 'poca' = pocos partidos, aún no concluyente.</i>")
    notifier.send_document(path, caption)
    print(f"[REPORT] jugador-hora enviado ({n} filas)")
    return n


def generate_and_send(notifier, days: int = 3) -> int:
    """Genera y envía AMBOS Excel: jugador-equipo y jugador-hora. Devuelve filas del 1º."""
    source = ESBSource()
    players = alerted_players()
    agg = build(source, players, days=days)
    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(_OUT_DIR, f"jugador-equipo_{fecha}.csv")
    n = write_csv(agg, path)
    caption = (f"📗 <b>Reporte jugador–equipo</b> (últimos {days} días)\n"
               f"{n} combinaciones jugador+equipo. Ábrelo en Excel.\n"
               f"<i>Muestra 'poca' = pocos partidos, tómalo con pinzas.</i>")
    notifier.send_document(path, caption)
    print(f"[REPORT] jugador-equipo enviado ({n} filas)")
    # Segundo Excel: jugador-hora (franjas en que juega y su rendimiento).
    try:
        generate_hours_and_send(notifier)
    except Exception as e:
        print(f"[WARN] jugador-hora: {e}")
    return n
