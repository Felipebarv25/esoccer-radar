"""Reporte de ENFRENTAMIENTO entre dos jugadores con equipos concretos.

Formato del comando:
    /{Nick}_{Equipo} versus {Nick2}_{Equipo2}
    (también sirve "vs" en vez de "versus")

Devuelve: head-to-head total, H2H con ESOS equipos, últimos 10 con la combinación,
promedio de goles, Over/BTTS, Elo esperado, win% de cada uno con su equipo y racha.
Todo son datos observados, no un pronóstico.
"""
import concurrent.futures
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

import analytics
import elo
import player_profile
import team_ratings
from esb_source import ESBSource

_COL = timezone(timedelta(hours=-5))
_SPLIT = re.compile(r"\s+(?:versus|vs)\s+", re.IGNORECASE)
_CACHE = {}          # (a_low, b_low) -> (ts, matches)
_CACHE_TTL = 900     # 15 min: el endpoint H2H de la API es lento (~6s), así que
#                      la 1ª vez cuesta pero las repeticiones salen al instante.


def parse_side(s: str):
    """'Nick_Equipo' -> (canon_nick|None, equipo|None). Resuelve el nick real."""
    s = s.strip().lstrip("/").strip()
    if "_" not in s:
        return player_profile.resolve(s), None
    parts = s.split("_")
    for k in range(len(parts) - 1, 0, -1):
        nick = "_".join(parts[:k])
        team = " ".join(parts[k:]).strip()
        canon = player_profile.resolve(nick)
        if canon:
            return canon, (team or None)
    return None, None


def _h2h_matches(source, a, b, pages=3, workers=3):
    """Historial H2H. Cachea por pareja y baja las páginas EN PARALELO de una."""
    key = (a.lower(), b.lower())
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]

    headers = dict(source.session.headers)
    url = f"{source.base}/participants/{quote(a)}/compare/{quote(b)}/matches"

    def get(page):
        try:
            r = requests.get(url, headers=headers, params={"page": page}, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception:
            return {}

    # pedimos las 3 páginas a la vez (ciegas) → el tiempo es el de la más lenta
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        datas = list(ex.map(get, range(1, pages + 1)))

    matches = []
    for data in datas:
        for m in data.get("matches", []):
            p1, p2 = m.get("participant1", {}), m.get("participant2", {})
            s1, s2 = p1.get("score"), p2.get("score")
            if s1 is None or s2 is None:
                continue
            if (p1.get("nickname") or "").lower() == a.lower():
                ta = (p1.get("team") or {}).get("token_international")
                tb = (p2.get("team") or {}).get("token_international")
                sa, sb = s1, s2
            else:
                ta = (p2.get("team") or {}).get("token_international")
                tb = (p1.get("team") or {}).get("token_international")
                sa, sb = s2, s1
            matches.append({"date": m.get("date"), "ta": ta, "tb": tb, "sa": sa, "sb": sb})
    _CACHE[key] = (time.time(), matches)
    return matches


def _agg(ms, a, b):
    n = len(ms)
    aw = sum(1 for m in ms if m["sa"] > m["sb"])
    bw = sum(1 for m in ms if m["sb"] > m["sa"])
    dr = n - aw - bw
    gf = sum(m["sa"] + m["sb"] for m in ms)
    o25 = sum(1 for m in ms if m["sa"] + m["sb"] >= 3)
    o35 = sum(1 for m in ms if m["sa"] + m["sb"] >= 4)
    btts = sum(1 for m in ms if m["sa"] > 0 and m["sb"] > 0)
    return {"n": n, "aw": aw, "bw": bw, "dr": dr,
            "avg": round(gf / n, 2) if n else None,
            "o25": round(100 * o25 / n) if n else None,
            "o35": round(100 * o35 / n) if n else None,
            "btts": round(100 * btts / n) if n else None}


def _streak(views, player, n=6):
    rows = [v for v in views if v.get("start_col") and v["outcome"] in
            ("acierto", "fallo", "empate") and player in (v["p1"], v["p2"])]
    rows.sort(key=lambda v: v["start_col"], reverse=True)
    out = []
    for v in rows[:n]:
        if v["winner"] == player:
            out.append("✅")
        elif v["winner"] is None:
            out.append("➖")
        else:
            out.append("❌")
    return "".join(out) if out else "s/d"


def build(full_text: str) -> str:
    sides = _SPLIT.split(full_text.strip(), maxsplit=1)
    if len(sides) != 2:
        return ("Formato: <code>/Nick_Equipo versus Nick2_Equipo2</code>\n"
                "Ej: <code>/DEKSON_Arsenal versus Sheva_Bayern Munchen</code>")
    a, team_a = parse_side(sides[0])
    b, team_b = parse_side(sides[1])
    if not a or not b:
        falta = sides[0] if not a else sides[1]
        return f"No reconocí al jugador en «{falta.strip()}». Revisa el nick."

    source = ESBSource()
    ms = _h2h_matches(source, a, b)
    overall = _agg(ms, a, b)

    # combo con los equipos pedidos (si se dieron)
    combo = ms
    if team_a and team_b:
        ta_l, tb_l = team_a.lower(), team_b.lower()
        combo = [m for m in ms if (m["ta"] or "").lower() == ta_l
                 and (m["tb"] or "").lower() == tb_l]
    cagg = _agg(combo, a, b)

    views = analytics.load_views()
    ra, _ = elo.get(a)
    rb, _ = elo.get(b)
    exp = elo.expected(ra, rb)
    fav = a if exp >= 0.5 else b
    prob = round(100 * (exp if exp >= 0.5 else 1 - exp))
    twa = team_ratings.get(a, team_a) if team_a else (0, 0, None)
    twb = team_ratings.get(b, team_b) if team_b else (0, 0, None)

    L = [f"⚔️ <b>ENFRENTAMIENTO</b>",
         f"<b>{a}</b>{f' ({team_a})' if team_a else ''}  vs  "
         f"<b>{b}</b>{f' ({team_b})' if team_b else ''}\n"]
    L.append(f"🔢 <b>Elo:</b> {a} {round(ra)} vs {b} {round(rb)} → esperado "
             f"<b>{fav} {prob}%</b>")
    if team_a and twa[2] is not None:
        L.append(f"🏟️ {a} con {team_a}: {round(100 * twa[2])}% ({twa[1]}/{twa[0]})")
    if team_b and twb[2] is not None:
        L.append(f"🏟️ {b} con {team_b}: {round(100 * twb[2])}% ({twb[1]}/{twb[0]})")

    if overall["n"]:
        L.append(f"\n⚔️ <b>H2H total</b> ({overall['n']}): {a} "
                 f"{overall['aw']}-{overall['dr']}-{overall['bw']} {b}")
        L.append(f"⚽ Goles: prom {overall['avg']} · O2.5 {overall['o25']}% · "
                 f"O3.5 {overall['o35']}% · BTTS {overall['btts']}%")
    else:
        L.append(f"\n⚔️ Sin enfrentamientos previos entre {a} y {b}.")

    if team_a and team_b:
        if cagg["n"]:
            L.append(f"\n🎯 <b>H2H con {team_a} vs {team_b}</b> ({cagg['n']}): "
                     f"{a} {cagg['aw']}-{cagg['dr']}-{cagg['bw']} {b} · "
                     f"prom {cagg['avg']} goles")
            L.append("📋 Últimos con esa combinación:")
            for m in combo[:10]:
                d = (m["date"] or "")[:10]
                L.append(f"• {a} {m['sa']}-{m['sb']} {b}  ({d})")
        else:
            L.append(f"\n🎯 No hay H2H previo con esa combinación exacta "
                     f"({team_a} vs {team_b}).")

    L.append(f"\n📈 <b>Racha reciente</b> (nuestros datos):")
    L.append(f"• {a}: {_streak(views, a)}   • {b}: {_streak(views, b)}")
    L.append("\n<i>Datos observados, NO probabilidad ni pronóstico.</i>")
    return "\n".join(L)


def generate_and_send(notifier, full_text):
    notifier.send(build(full_text))
