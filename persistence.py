"""Guarda cada análisis y su desenlace para backtesting (score vs resultado real).

Cada registro guarda además un `features`: la FOTO de todas las variables
pre-partido (congeladas ANTES de jugar) para armar el dataset del modelo (#7)
sin fuga de datos (leakage). Nunca metemos aquí nada que dependa del resultado.
"""
import glob
import json
import os
from datetime import datetime, timedelta, timezone

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "matches")
_COL = timezone(timedelta(hours=-5))


def _path(mid) -> str:
    return os.path.join(_DIR, f"{mid}.json")


def _parse(iso):
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except Exception:
        return None


def build_features(meta: dict, analysis: dict, elo: dict = None) -> dict:
    """Vector de variables PRE-partido (leak-free) para el dataset del modelo."""
    ca = analysis.get("career", {}).get("a", {}) or {}
    cb = analysis.get("career", {}).get("b", {}) or {}
    fa = analysis.get("recent_form", {}).get("a", {}) or {}
    fb = analysis.get("recent_form", {}).get("b", {}) or {}
    h = analysis.get("h2h", {}) or {}

    hour_col = weekday = None
    dt = _parse(meta.get("date"))
    if dt:
        c = dt.astimezone(_COL)
        hour_col, weekday = c.hour, c.weekday()

    feats = {
        # contexto
        "match_type": meta.get("match_type"),
        "hour_col": hour_col, "weekday": weekday,
        # score casero (para comparar contra el Elo)
        "score_a": analysis.get("score_a"),
        # carrera
        "career_wr_a": ca.get("win_rate"), "career_wr_b": cb.get("win_rate"),
        "career_matches_a": ca.get("matches"), "career_matches_b": cb.get("matches"),
        # forma reciente
        "form_gf_a": fa.get("gf_per_game"), "form_ga_a": fa.get("ga_per_game"),
        "form_wr_a": fa.get("win_rate"), "form_games_a": fa.get("games"),
        "form_gf_b": fb.get("gf_per_game"), "form_ga_b": fb.get("ga_per_game"),
        "form_wr_b": fb.get("win_rate"), "form_games_b": fb.get("games"),
        # head-to-head
        "h2h_matches": h.get("matches"), "h2h_a_win": h.get("a_win"),
        "h2h_draw": h.get("draw"), "h2h_b_win": h.get("b_win"),
        "h2h_avg_goals": h.get("avg_total_goals"),
        "h2h_over25": h.get("over_2_5"), "h2h_over35": h.get("over_3_5"),
        "h2h_over45": h.get("over_4_5"), "h2h_btts": h.get("btts"),
    }
    if elo:
        feats.update({
            "elo_a": elo.get("elo_a"), "elo_b": elo.get("elo_b"),
            "elo_exp_a": elo.get("exp_a"),
            "elo_games_a": elo.get("games_a"), "elo_games_b": elo.get("games_b"),
        })
    td = analysis.get("team_detail") or {}
    feats["team_wr_a"] = td.get("wr_a")
    feats["team_wr_b"] = td.get("wr_b")
    return feats


def save_analysis(meta: dict, analysis: dict, message_id=None, elo: dict = None) -> dict:
    os.makedirs(_DIR, exist_ok=True)
    mid = meta.get("match_id")
    # favorito real (el del score > 50); si score==50, no hay favorito
    sc = analysis.get("score_a", 50)
    if sc > 50:
        favored = analysis["player_a"]
    elif sc < 50:
        favored = analysis["player_b"]
    else:
        favored = None
    rec = {
        "match_id": mid,
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "date": meta.get("date"),
        "match_type": meta.get("match_type"),
        "player1": meta.get("player1"), "team1": meta.get("team1"),
        "player2": meta.get("player2"), "team2": meta.get("team2"),
        "score_a": sc,
        "favored": favored,
        "message_id": message_id,
        "features": build_features(meta, analysis, elo),  # foto pre-partido (leak-free)
        "result": None,   # se completa al cerrar (backtesting)
    }
    with open(_path(mid), "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=2)
    return rec


def already_saved_ids() -> set:
    return {os.path.splitext(os.path.basename(p))[0]
            for p in glob.glob(os.path.join(_DIR, "*.json"))}


def load_open() -> list:
    """Registros aún sin desenlace (result == None). Para reintentar al cerrar."""
    out = []
    for p in glob.glob(os.path.join(_DIR, "*.json")):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                rec = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        if rec.get("result") is None:
            out.append(rec)
    return out


def set_result(rec: dict, result: dict) -> None:
    """Cierra un registro con su desenlace y lo reescribe."""
    rec["result"] = result
    try:
        with open(_path(rec["match_id"]), "w", encoding="utf-8") as fh:
            json.dump(rec, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass
