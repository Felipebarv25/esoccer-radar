"""Ligas/"grupos" que se están jugando AHORA + clima del mercado.

En eSoccer, a cada rato corren varias ligas a la vez (Serie A, Champions, etc.),
cada una con su grupo de jugadores jugando entre sí durante ~1-2 horas. Este
módulo detecta qué ligas están activas ahora, qué jugadores compiten y quiénes
son los más fuertes, más un "clima del mercado" reciente (favorito gana X%, goles).

Se usa en el comando /grupos y en una alerta periódica (cada 15 min).
"""
import concurrent.futures
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import analytics
import elo
from esb_source import ESBSource, _FINISHED_TOURNAMENT, match_type

_COL = timezone(timedelta(hours=-5))
_SNAP = {"ts": 0.0, "data": None}


def _league_name(t):
    lg = (t.get("league") or {}).get("token_international")
    return lg or (t.get("token_international") or "?").rsplit(" 20", 1)[0]


def compute(source: ESBSource = None, back_min=80, fwd_min=45, workers=6):
    source = source or ESBSource()
    now = datetime.now(timezone.utc)
    lo, hi = now - timedelta(minutes=back_min), now + timedelta(minutes=fwd_min)
    headers = dict(source.session.headers)
    df = (now - timedelta(hours=2)).strftime("%Y/%m/%d %H:%M")
    dt = (now + timedelta(hours=1)).strftime("%Y/%m/%d %H:%M")

    # torneos no terminados en la ventana
    tour = {}
    page, total = 1, 1
    while page <= total and page <= 15:
        try:
            data = source._get("/tournaments", params={"page": page, "dateFrom": df, "dateTo": dt})
        except Exception:
            break
        total = data.get("totalPages", 1)
        for t in data.get("tournaments", []):
            if t.get("status_id") != _FINISHED_TOURNAMENT:
                tour[t["id"]] = t
        page += 1

    import requests

    def fetch(tid):
        r = requests.get(f"{source.base}/tournaments/{tid}/matches", headers=headers, timeout=20)
        r.raise_for_status()
        return r.json()

    leagues = defaultdict(lambda: {"n": 0, "players": set(), "type": "2x4"})
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch, tid): t for tid, t in tour.items()}
        for fut in concurrent.futures.as_completed(futs):
            t = futs[fut]
            try:
                data = fut.result()
            except Exception:
                continue
            arr = data if isinstance(data, list) else (data.get("matches") or [])
            active = False
            players = set()
            for m in arr:
                try:
                    md = datetime.fromisoformat((m.get("date") or "").replace("Z", "+00:00"))
                except Exception:
                    continue
                if lo <= md <= hi:
                    active = True
                    for k in ("participant1", "participant2"):
                        nk = (m.get(k) or {}).get("nickname")
                        if nk:
                            players.add(nk)
            if active:
                name = _league_name(t)
                typ = match_type(t.get("token_international"))
                e = leagues[(name, typ)]
                e["n"] += 1
                e["type"] = typ
                e["players"].update(players)

    # clima del mercado: última hora en nuestros datos
    views = analytics.load_views()
    end = datetime.now(_COL)
    win = analytics.in_window(views, end - timedelta(hours=1), end + timedelta(minutes=1))
    sb = analytics.scoreboard(win)
    goals = analytics.goals_summary(win)

    rows = []
    for (name, typ), e in leagues.items():
        pls = sorted(e["players"], key=lambda p: elo.get(p)[0], reverse=True)
        rows.append({"name": name, "type": typ, "n": e["n"],
                     "players": pls, "n_players": len(pls)})
    rows.sort(key=lambda r: r["n"], reverse=True)
    snap = {"ts": time.time(), "leagues": rows,
            "market": {"fav_rate": sb.get("tasa_estricta"), "n": sb.get("conpick"),
                       "avg_goals": (goals or {}).get("avg")}}
    _SNAP["ts"] = time.time()
    _SNAP["data"] = snap
    return snap


def set_snapshot(snap):
    _SNAP["ts"] = time.time()
    _SNAP["data"] = snap


def get_or_compute(max_age=420):
    d = _SNAP["data"]
    if d and time.time() - _SNAP["ts"] < max_age:
        return d
    return compute()


def _market_line(mk):
    if not mk or not mk.get("n"):
        return "🌡️ Mercado (última hora): sin datos aún."
    g = f" · goles {mk['avg']}" if mk.get("avg") is not None else ""
    return (f"🌡️ <b>Mercado última hora:</b> favorito gana {mk['fav_rate']}% "
            f"(n={mk['n']}){g}")


def text_report(snap) -> str:
    if not snap or not snap.get("leagues"):
        return ("🎮 <b>Grupos en juego</b>\nNo detecté ligas activas en este momento "
                "(puede ser una pausa entre sesiones).")
    L = ["🎮 <b>Grupos en juego AHORA</b>", _market_line(snap.get("market")), ""]
    for lg in snap["leagues"][:10]:
        L.append(f"🏆 <b>{lg['name']}</b> ({lg['type']}) — {lg['n']} partidos · "
                 f"{lg['n_players']} jugadores")
        fuertes = lg["players"][:4]
        if fuertes:
            chips = " · ".join(f"{p} ({round(elo.get(p)[0])})" for p in fuertes)
            L.append(f"   ⭐ Más fuertes (Elo): {chips}")
    L.append("\n<i>Datos observados, NO pronóstico. El Elo mide fuerza, no garantiza.</i>")
    return "\n".join(L)


def text_alert(snap) -> str:
    if not snap or not snap.get("leagues"):
        return ""
    ligas = ", ".join(f"{lg['name']} ({lg['type']})" for lg in snap["leagues"][:8])
    return (f"🎮 <b>Jugándose ahora:</b> {ligas}\n{_market_line(snap.get('market'))}\n"
            f"<i>Pide /grupos para el detalle por liga.</i>")
