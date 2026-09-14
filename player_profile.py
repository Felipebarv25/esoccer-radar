"""Perfil individual de un jugador (comando /detalle_jugadores y /<nick>).

Arma, a pedido, las estadísticas personales de un jugador desde la API oficial:
partidos, goles, equipos con los que juega (y su rendimiento por equipo),
antigüedad, W/D/L, y los rivales a los que suele ganar/perder.

Es INFO observada de lo ya jugado, no un pronóstico.
"""
import concurrent.futures
import time
from collections import defaultdict
from datetime import datetime, timezone

import requests

import elo
from esb_source import ESBSource, _PLAYED_TOURNAMENTS, career_summary

_DIAS_MES = 30.44


def known_players() -> set:
    """Todos los nicks con Elo (los que hemos visto jugar)."""
    return set(elo._load()["ratings"].keys())


def resolve(nick: str):
    """Nick canónico exacto (case-insensitive) o None."""
    low = (nick or "").lower()
    for n in known_players():
        if n.lower() == low:
            return n
    return None


def candidates(nick: str, limit: int = 12):
    low = (nick or "").lower()
    return sorted((n for n in known_players() if low in n.lower()),
                  key=str.lower)[:limit]


def players_page(page: int = 1, per_page: int = 80, min_games: int = 10) -> str:
    """Listado paginado de jugadores activos, cada uno como comando /<nick>."""
    ratings = elo._load()["ratings"]
    rows = [(n, r["rating"], r["games"]) for n, r in ratings.items()
            if r["games"] >= min_games]
    rows.sort(key=lambda x: x[1], reverse=True)
    total = len(rows)
    if total == 0:
        return ("👥 <b>Detalle jugadores</b>\nAún no hay jugadores en el Elo. "
                "Siembra el histórico con elo_bootstrap.")
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, pages))
    chunk = rows[(page - 1) * per_page: page * per_page]
    out = [f"👥 <b>Detalle jugadores</b> — {total} activos (≥{min_games} part.) · "
           f"pág {page}/{pages}",
           "Toca un nombre para ver sus estadísticas:"]
    for n, rt, g in chunk:
        out.append(f"/{n} · Elo {round(rt)}")
    if page < pages:
        out.append(f"\n➡️ Siguiente: /detalle_jugadores {page + 1}")
    out.append("<i>¿No está en la lista? Escribe /su_nick directamente.</i>")
    return "\n".join(out)


def _get_json(url, headers, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, headers=headers, timeout=20)
            if r.status_code == 429:
                time.sleep(1 + i)
                continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(0.5 + i)


def build(nick: str, source: ESBSource = None, max_tournaments: int = 40,
          workers: int = 8) -> dict:
    """Perfil del jugador desde sus últimos torneos jugados + carrera + Elo."""
    source = source or ESBSource()
    headers = dict(source.session.headers)

    # 1) torneos del jugador (recientes) + antigüedad (torneo más viejo)
    tids, oldest = [], None
    try:
        first = source.participant_tournaments(nick, page=1)
        total_pages = first.get("totalPages", 1)
        for t in (first.get("tournaments") or []):
            if t.get("status_id") in _PLAYED_TOURNAMENTS:
                tids.append(t["id"])
        # una página más si hace falta para llenar la muestra
        p = 2
        while len(tids) < max_tournaments and p <= total_pages and p <= 4:
            more = source.participant_tournaments(nick, page=p)
            for t in (more.get("tournaments") or []):
                if t.get("status_id") in _PLAYED_TOURNAMENTS:
                    tids.append(t["id"])
            p += 1
        # última página → torneo más antiguo (antigüedad)
        if total_pages > 1:
            last = source.participant_tournaments(nick, page=total_pages)
            fechas = [t.get("date") for t in (last.get("tournaments") or []) if t.get("date")]
            if fechas:
                oldest = min(fechas)
    except Exception:
        pass
    tids = tids[:max_tournaments]

    # 2) partidos de esos torneos (en paralelo)
    def fetch(tid):
        return _get_json(f"{source.base}/tournaments/{tid}/matches", headers)

    per_team = defaultdict(lambda: {"g": 0, "w": 0, "gf": 0, "ga": 0})
    per_opp = defaultdict(lambda: {"g": 0, "w": 0, "l": 0})
    tot = {"g": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0}
    mdates = []
    low = nick.lower()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in concurrent.futures.as_completed([ex.submit(fetch, t) for t in tids]):
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
                team = (me.get("team") or {}).get("token_international", "?")
                oppn = opp.get("nickname") or "?"
                won, drew = sm > so, sm == so
                tot["g"] += 1; tot["gf"] += sm; tot["ga"] += so
                tot["w"] += won; tot["d"] += drew; tot["l"] += (sm < so)
                t = per_team[team]
                t["g"] += 1; t["w"] += won; t["gf"] += sm; t["ga"] += so
                o = per_opp[oppn]
                o["g"] += 1; o["w"] += won; o["l"] += (sm < so)
                if m.get("date"):
                    mdates.append(m["date"])

    # 3) carrera total (histórico completo) + Elo
    career = None
    try:
        career = career_summary(source.participant(nick))
    except Exception:
        pass
    elo_r, elo_g = elo.get(nick)

    return {"nick": nick, "tot": tot, "per_team": dict(per_team),
            "per_opp": dict(per_opp), "career": career,
            "elo": round(elo_r), "elo_games": elo_g,
            "oldest": oldest or (min(mdates) if mdates else None),
            "n_tournaments": len(tids)}


def _antiguedad(iso):
    try:
        d = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except Exception:
        return None
    dias = (datetime.now(timezone.utc) - d).total_seconds() / 86400
    if dias >= 60:
        return f"hace ~{dias / _DIAS_MES:.0f} meses ({d:%Y-%m-%d})"
    return f"hace ~{dias:.0f} días ({d:%Y-%m-%d})"


def format_profile(p: dict) -> str:
    nick = p["nick"]
    tot = p["tot"]
    g = tot["g"]
    if g == 0:
        return (f"👤 <b>{nick}</b> · Elo {p['elo']} ({p['elo_games']} part.)\n"
                "No encontré partidos recientes con marcador para este jugador.")
    wr = round(100 * tot["w"] / g)
    gf_pg = round(tot["gf"] / g, 2)
    ga_pg = round(tot["ga"] / g, 2)
    tot_pg = round((tot["gf"] + tot["ga"]) / g, 2)

    L = [f"👤 <b>{nick}</b> · 🔢 Elo <b>{p['elo']}</b> ({p['elo_games']} part.)"]
    if p.get("oldest"):
        L.append(f"🗓️ Juega desde {_antiguedad(p['oldest'])}")
    L.append(f"🎮 Muestra reciente ({p['n_tournaments']} torneos): <b>{g}</b> "
             f"partidos · ✅ {tot['w']} ➖ {tot['d']} ❌ {tot['l']} · "
             f"win <b>{wr}%</b>")
    L.append(f"⚽ Goles: {gf_pg} a favor · {ga_pg} en contra · {tot_pg} por partido")
    c = p.get("career")
    if c and c.get("matches"):
        cwr = f"{round(100 * c['win_rate'])}%" if c.get("win_rate") is not None else "—"
        L.append(f"📊 Carrera total: {c['win']}-{c['draw']}-{c['lose']} de "
                 f"{c['matches']} ({cwr})")

    # equipos (por nº de partidos), con win% y goles a favor/p
    teams = sorted(p["per_team"].items(), key=lambda kv: kv[1]["g"], reverse=True)
    if teams:
        L.append("\n🛡️ <b>Equipos con los que juega</b> (win% · goles/p):")
        for name, t in teams[:8]:
            twr = round(100 * t["w"] / t["g"])
            L.append(f"• {name} — {twr}% ({t['w']}/{t['g']}) · "
                     f"{round(t['gf'] / t['g'], 1)}⚽")

    # rivales (mínimo 3 duelos para que signifique algo); sin solaparse
    opps = [(n, o, o["w"] / o["g"]) for n, o in p["per_opp"].items() if o["g"] >= 3]
    faciles = sorted((x for x in opps if x[2] >= 0.60), key=lambda x: (x[2], x[1]["g"]),
                     reverse=True)
    dificiles = sorted((x for x in opps if x[2] <= 0.40), key=lambda x: (x[2], -x[1]["g"]))
    if faciles:
        L.append("\n😀 <b>Les gana fácil</b>:")
        for n, o, wr_ in faciles[:5]:
            L.append(f"• {n} — gana {round(100 * wr_)}% ({o['w']}/{o['g']})")
    if dificiles:
        L.append("\n😰 <b>Se le complican</b>:")
        for n, o, wr_ in dificiles[:5]:
            L.append(f"• {n} — solo gana {round(100 * wr_)}% ({o['w']}/{o['g']})")

    L.append("\n<i>Datos observados de lo ya jugado, no pronóstico. "
             "Muestra reciente (últimos torneos) salvo la carrera total.</i>")
    return "\n".join(L)


def generate_and_send(notifier, raw_nick: str) -> bool:
    canon = resolve(raw_nick)
    if not canon:
        cands = candidates(raw_nick)
        if cands:
            notifier.send("🔎 ¿Cuál de estos? " + " ".join(f"/{c}" for c in cands))
        else:
            notifier.send(f"No tengo datos de «{raw_nick}».")
        return False
    prof = build(canon)
    notifier.send(format_profile(prof))
    return True
