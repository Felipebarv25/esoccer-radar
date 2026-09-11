"""Reporte por JUGADOR + EQUIPO (#5c): CSV que abre en Excel.

Para ver si un jugador rinde bien con TODOS los equipos o solo con algunos
(la observación "bueno con el City, malo con el Madrid"). Recorre los torneos
TERMINADOS de los últimos días y agrega, por (jugador, equipo): partidos, W/D/L,
% de victoria, goles a favor/contra por partido.

HONESTO: por (jugador, equipo) la muestra suele ser pequeña. Cada fila trae su
número de partidos para que sepas cuánto pesa; pocas partidas = anecdótico.
"""
import csv
import glob
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

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


def build(source: ESBSource, players: set, days: int = 3, max_pages: int = 60) -> dict:
    now = datetime.now(timezone.utc)
    df = (now - timedelta(days=days)).strftime("%Y/%m/%d %H:%M")
    dt = now.strftime("%Y/%m/%d %H:%M")
    agg = defaultdict(lambda: {"games": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0})
    page, total = 1, 1
    while page <= total and page <= max_pages:
        data = source._get("/tournaments", params={"page": page, "dateFrom": df, "dateTo": dt})
        total = data.get("totalPages", 1)
        for t in data.get("tournaments", []):
            if t.get("status_id") != _FINISHED_TOURNAMENT:
                continue
            res = source.tournament_results(t["id"])
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
        page += 1
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


def generate_and_send(notifier, days: int = 3) -> int:
    """Genera el CSV jugador-equipo y lo envía al canal. Devuelve nº de filas."""
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
    return n
