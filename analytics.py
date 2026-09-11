"""Análisis agregados sobre los partidos cerrados (para el canal de reportes).

Todo sale de data/matches/*.json (registros con `result`). Son DATOS OBSERVADOS
de lo que ya pasó, NO probabilidades, pronósticos ni garantías. Con muestras
chicas nada de esto es concluyente — por eso cada ranking lleva su `n`.

Convenciones de outcome (las pone backtest.py):
  acierto  = el favorito ganó
  fallo    = el favorito perdió
  empate   = terminó empatado (nadie ganó)
  parejo   = no había favorito claro (score ~50) → no había "pick"
  sin_datos= no apareció el marcador
"""
import glob
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

_COL = timezone(timedelta(hours=-5))
_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "matches")
_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
# outcomes que representan un partido cerrado CON pick decidible (favorito sí/no ganó)
_CONPICK = ("acierto", "fallo", "empate")


def _parse(iso):
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except Exception:
        return None


def load_views():
    """Lee todos los registros y los normaliza a una vista uniforme."""
    views = []
    for p in glob.glob(os.path.join(_DIR, "*.json")):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                rec = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        res = rec.get("result") or {}
        start = _parse(rec.get("date"))
        views.append({
            "match_id": rec.get("match_id"),
            "start_col": start.astimezone(_COL) if start else None,
            "type": rec.get("match_type"),
            "p1": rec.get("player1"), "t1": rec.get("team1"),
            "p2": rec.get("player2"), "t2": rec.get("team2"),
            "favored": rec.get("favored"),
            "score_a": rec.get("score_a"),
            "outcome": res.get("outcome"),
            "hit": res.get("hit"),
            "winner": res.get("winner"),
            "sa": res.get("sa"), "sb": res.get("sb"),
            "closed": bool(res),
        })
    return views


def in_window(views, start_col, end_col):
    """Filtra por hora de INICIO del partido (hora Colombia) en [start, end)."""
    out = []
    for v in views:
        s = v["start_col"]
        if s is not None and start_col <= s < end_col:
            out.append(v)
    return out


# ---------- tableros ----------
def scoreboard(views) -> dict:
    """Conteo de una tanda de partidos + dos tasas (estricta y entre decididos)."""
    b = {"analizados": 0, "acierto": 0, "fallo": 0, "empate": 0,
         "parejo": 0, "sin_datos": 0, "pendiente": 0}
    for v in views:
        b["analizados"] += 1
        if not v["closed"]:
            b["pendiente"] += 1
            continue
        o = v["outcome"]
        if o in b:
            b[o] += 1
    decididos = b["acierto"] + b["fallo"]              # con ganador
    conpick = b["acierto"] + b["fallo"] + b["empate"]  # había favorito (empate cuenta)
    b["decididos"] = decididos
    b["conpick"] = conpick
    # tasa estricta: el favorito GANÓ (empate NO es acierto) — como apuesta a ganar
    b["tasa_estricta"] = round(100 * b["acierto"] / conpick) if conpick else None
    # tasa entre decididos: solo partidos que tuvieron ganador
    b["tasa_decididos"] = round(100 * b["acierto"] / decididos) if decididos else None
    return b


def player_perf(views, min_n=1):
    """Rendimiento REAL de cada jugador en los partidos observados (cerrados)."""
    agg = defaultdict(lambda: {"n": 0, "w": 0, "gf": 0, "ga": 0})
    for v in views:
        if v["outcome"] not in _CONPICK:
            continue
        for who, gf, ga in ((v["p1"], v["sa"], v["sb"]), (v["p2"], v["sb"], v["sa"])):
            if not who:
                continue
            a = agg[who]
            a["n"] += 1
            if gf is not None and ga is not None:
                a["gf"] += gf
                a["ga"] += ga
            if v["winner"] == who:
                a["w"] += 1
    rows = []
    for name, a in agg.items():
        if a["n"] < min_n:
            continue
        rows.append({
            "name": name, "n": a["n"], "w": a["w"],
            "wr": round(100 * a["w"] / a["n"]) if a["n"] else 0,
            "gpg": round(a["gf"] / a["n"], 2) if a["n"] else None,
        })
    rows.sort(key=lambda r: (r["wr"], r["w"], r["n"]), reverse=True)
    return rows


def team_perf(views, min_n=1):
    """Rendimiento por EQUIPO (mezcla todos los jugadores que lo usaron)."""
    agg = defaultdict(lambda: {"n": 0, "w": 0})
    for v in views:
        if v["outcome"] not in _CONPICK:
            continue
        for team, who in ((v["t1"], v["p1"]), (v["t2"], v["p2"])):
            if not team:
                continue
            a = agg[team]
            a["n"] += 1
            if v["winner"] == who:
                a["w"] += 1
    rows = [{"name": t, "n": a["n"], "w": a["w"],
             "wr": round(100 * a["w"] / a["n"]) if a["n"] else 0}
            for t, a in agg.items() if a["n"] >= min_n]
    rows.sort(key=lambda r: (r["wr"], r["n"]), reverse=True)
    return rows


def player_team(views, min_n=2):
    """Rendimiento de cada combinación (jugador, equipo) — el 'bueno con City'."""
    agg = defaultdict(lambda: {"n": 0, "w": 0})
    for v in views:
        if v["outcome"] not in _CONPICK:
            continue
        for team, who in ((v["t1"], v["p1"]), (v["t2"], v["p2"])):
            if not team or not who:
                continue
            a = agg[(who, team)]
            a["n"] += 1
            if v["winner"] == who:
                a["w"] += 1
    rows = [{"player": k[0], "team": k[1], "n": a["n"], "w": a["w"],
             "wr": round(100 * a["w"] / a["n"]) if a["n"] else 0}
            for k, a in agg.items() if a["n"] >= min_n]
    rows.sort(key=lambda r: (r["wr"], r["n"]), reverse=True)
    return rows


def player_names(views):
    """Conjunto de todos los nicknames vistos (para buscar por comando)."""
    s = set()
    for v in views:
        if v["p1"]:
            s.add(v["p1"])
        if v["p2"]:
            s.add(v["p2"])
    return s


def player_team_breakdown(views, player, min_n=1):
    """Rendimiento de UN jugador equipo por equipo (con cuáles gana/pierde)."""
    agg = defaultdict(lambda: {"n": 0, "w": 0})
    for v in views:
        if v["outcome"] not in _CONPICK:
            continue
        for team, who in ((v["t1"], v["p1"]), (v["t2"], v["p2"])):
            if who != player or not team:
                continue
            a = agg[team]
            a["n"] += 1
            if v["winner"] == who:
                a["w"] += 1
    rows = [{"team": t, "n": a["n"], "w": a["w"],
             "wr": round(100 * a["w"] / a["n"]) if a["n"] else 0}
            for t, a in agg.items() if a["n"] >= min_n]
    rows.sort(key=lambda r: (r["wr"], r["n"]), reverse=True)
    return rows


def player_hour_rows(views):
    """Filas jugador+hora (para el Excel): en qué franjas juega y cómo le va.

    Sale de NUESTROS registros cerrados (traen la hora COL y el resultado).
    """
    agg = defaultdict(lambda: {"n": 0, "w": 0, "gf": 0, "ga": 0})
    for v in views:
        if v["outcome"] not in _CONPICK or v["start_col"] is None:
            continue
        h = v["start_col"].hour
        for who, gf, ga in ((v["p1"], v["sa"], v["sb"]), (v["p2"], v["sb"], v["sa"])):
            if not who:
                continue
            a = agg[(who, h)]
            a["n"] += 1
            if gf is not None and ga is not None:
                a["gf"] += gf
                a["ga"] += ga
            if v["winner"] == who:
                a["w"] += 1
    rows = []
    for (who, h), a in agg.items():
        g = a["n"]
        rows.append({
            "jugador": who, "hora": f"{h:02d}:00", "partidos": g,
            "G": a["w"], "win_%": round(100 * a["w"] / g) if g else 0,
            "goles_a_favor_pp": round(a["gf"] / g, 2) if g else 0,
            "goles_en_contra_pp": round(a["ga"] / g, 2) if g else 0,
            "muestra": "ok" if g >= 6 else "poca",
        })
    rows.sort(key=lambda r: (r["jugador"], r["hora"]))
    return rows


def weekday_board(views):
    """Aciertos por día de la semana (hora Colombia). EXPLORATORIO."""
    agg = defaultdict(lambda: {"conpick": 0, "acierto": 0})
    for v in views:
        if v["start_col"] is None or v["outcome"] not in _CONPICK:
            continue
        a = agg[v["start_col"].weekday()]
        a["conpick"] += 1
        if v["outcome"] == "acierto":
            a["acierto"] += 1
    out = []
    for wd in range(7):
        a = agg.get(wd)
        if not a or a["conpick"] == 0:
            continue
        out.append({"dia": _DIAS[wd], "n": a["conpick"],
                    "tasa": round(100 * a["acierto"] / a["conpick"])})
    return out


def hour_board(views, min_n=3):
    """Aciertos por franja horaria (hora de inicio, COL). EXPLORATORIO."""
    agg = defaultdict(lambda: {"conpick": 0, "acierto": 0})
    for v in views:
        if v["start_col"] is None or v["outcome"] not in _CONPICK:
            continue
        a = agg[v["start_col"].hour]
        a["conpick"] += 1
        if v["outcome"] == "acierto":
            a["acierto"] += 1
    out = []
    for h in range(24):
        a = agg.get(h)
        if not a or a["conpick"] < min_n:
            continue
        out.append({"franja": f"{h:02d}:00", "n": a["conpick"],
                    "tasa": round(100 * a["acierto"] / a["conpick"])})
    out.sort(key=lambda r: r["tasa"], reverse=True)
    return out


_BANDAS = [(50, 60), (60, 70), (70, 80), (80, 101)]


def score_calibration(views):
    """¿El score predice? Por banda de fuerza del favorito, % real de acierto.

    Esta es la métrica CLAVE: si el score sirve, a más fuerza → más acierto.
    Es la base honesta para cualquier modelo (#7).
    """
    agg = {b: {"n": 0, "acierto": 0} for b in _BANDAS}
    for v in views:
        if v["outcome"] not in _CONPICK or v["score_a"] is None:
            continue
        fav = v["score_a"] if v["score_a"] >= 50 else 100 - v["score_a"]
        for b in _BANDAS:
            if b[0] <= fav < b[1]:
                agg[b]["n"] += 1
                if v["outcome"] == "acierto":
                    agg[b]["acierto"] += 1
                break
    out = []
    for b in _BANDAS:
        a = agg[b]
        if a["n"] == 0:
            continue
        etq = f"{b[0]}–{b[1] - 1 if b[1] <= 100 else 100}"
        out.append({"banda": etq, "n": a["n"],
                    "tasa": round(100 * a["acierto"] / a["n"])})
    return out


# ---------- detección de ventajas pre-partido ("on fire") ----------
def _player_history(views, player):
    """Partidos CERRADOS de un jugador: (start_col, won, team, opp, hour)."""
    out = []
    for v in views:
        if v["outcome"] not in _CONPICK:
            continue
        if v["p1"] == player:
            out.append((v["start_col"], v["winner"] == player, v["t1"], v["p2"]))
        elif v["p2"] == player:
            out.append((v["start_col"], v["winner"] == player, v["t2"], v["p1"]))
    out.sort(key=lambda r: (r[0] or datetime.min.replace(tzinfo=_COL)))
    return out


def _rate(rows):
    n = len(rows)
    w = sum(1 for r in rows if r[1])
    return n, w, (round(100 * w / n) if n else 0)


def detect_edges(views, p1, t1, p2, t2, hour,
                 min_team=4, min_h2h=3, min_hour=6, min_form=5, min_wr=70):
    """Ventajas históricas (de NUESTROS datos) para un partido que va a empezar.

    Devuelve lista de strings ya formateados. Solo incluye lo que supera el
    candado de muestra + porcentaje (para no inventar 'on fire' con 1 partido).
    """
    edges = []
    for player, team, opp in ((p1, t1, p2), (p2, t2, p1)):
        if not player:
            continue
        hist = _player_history(views, player)
        if not hist:
            continue
        # con ese equipo
        if team:
            n, w, wr = _rate([r for r in hist if r[2] == team])
            if n >= min_team and wr >= min_wr:
                edges.append(f"🔥 <b>{player}</b> rinde con {team}: {wr}% ({w}/{n})")
        # contra ese rival
        if opp:
            n, w, wr = _rate([r for r in hist if r[3] == opp])
            if n >= min_h2h and wr >= min_wr:
                edges.append(f"⚔️ <b>{player}</b> domina a {opp}: {w}-{n - w} ({wr}%)")
        # en esa franja horaria
        if hour is not None:
            n, w, wr = _rate([r for r in hist
                              if r[0] is not None and r[0].hour == hour])
            if n >= min_hour and wr >= min_wr:
                edges.append(f"⏰ <b>{player}</b> fuerte a las {hour:02d}:00: {wr}% ({w}/{n})")
        # en racha (últimos N)
        recientes = hist[-6:]
        n, w, wr = _rate(recientes)
        if n >= min_form and wr >= min_wr:
            edges.append(f"📈 <b>{player}</b> en racha: {w}/{n} recientes")
    return edges


def goals_summary(views):
    """Sesgo de goles realizado: promedio, Over 2.5/3.5, ambos anotan (BTTS)."""
    n = tot = over25 = over35 = btts = 0
    for v in views:
        if v["sa"] is None or v["sb"] is None:
            continue
        g = v["sa"] + v["sb"]
        n += 1
        tot += g
        if g >= 3:
            over25 += 1
        if g >= 4:
            over35 += 1
        if v["sa"] > 0 and v["sb"] > 0:
            btts += 1
    if n == 0:
        return None
    return {"n": n, "avg": round(tot / n, 2),
            "over25": round(100 * over25 / n), "over35": round(100 * over35 / n),
            "btts": round(100 * btts / n)}
