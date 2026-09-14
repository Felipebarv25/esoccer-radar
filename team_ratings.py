"""Tabla de rendimiento por (JUGADOR, EQUIPO): win% de cada jugador con cada club.

Alimenta el componente de EQUIPO del Score (ver esb_score). Se siembra con el
histórico (elo_bootstrap) y se actualiza en vivo al cerrar cada partido.

Estado en data/team_ratings.json: {pt: {"player|team": {g, w}}, done: [match_ids]}.
Idempotente por match_id (bootstrap + vivo no se doble-cuentan).
"""
import json
import os
import tempfile

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_FILE = os.path.join(_DIR, "team_ratings.json")

_state = None


def _load():
    global _state
    if _state is None:
        try:
            with open(_FILE, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (json.JSONDecodeError, OSError):
            raw = {}
        _state = {"pt": raw.get("pt", {}), "done_set": set(raw.get("done", []))}
    return _state


def _save():
    s = _load()
    data = {"pt": s["pt"], "done": list(s["done_set"])}
    os.makedirs(_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, _FILE)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _key(player, team):
    return f"{player}|{team}"


def get(player, team):
    """(games, wins, win_rate|None) del jugador con ese equipo."""
    e = _load()["pt"].get(_key(player, team))
    if not e or e["g"] == 0:
        return (0, 0, None)
    return (e["g"], e["w"], e["w"] / e["g"])


def _bump(s, player, team, won):
    if not player or not team:
        return
    e = s["pt"].setdefault(_key(player, team), {"g": 0, "w": 0})
    e["g"] += 1
    if won:
        e["w"] += 1


def record(p1, t1, p2, t2, winner, match_id=None, save=True):
    """Suma un partido a las dos combinaciones (p1,t1) y (p2,t2). Idempotente."""
    s = _load()
    mid = None if match_id is None else str(match_id)
    if mid is not None and mid in s["done_set"]:
        return False
    _bump(s, p1, t1, winner == p1)
    _bump(s, p2, t2, winner == p2)
    if mid is not None:
        s["done_set"].add(mid)
    if save:
        _save()
    return True


def flush():
    _save()


def top(min_games=10, limit=30):
    """Combos (jugador, equipo) con mejor win%, con mínimo de partidos."""
    rows = []
    for key, e in _load()["pt"].items():
        if e["g"] < min_games or "|" not in key:
            continue
        player, team = key.split("|", 1)
        rows.append({"player": player, "team": team, "g": e["g"], "w": e["w"],
                     "wr": round(100 * e["w"] / e["g"])})
    rows.sort(key=lambda r: (r["wr"], r["g"]), reverse=True)
    return rows[:limit]
