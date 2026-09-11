"""Guarda cada análisis y su desenlace para backtesting (score vs resultado real)."""
import glob
import json
import os
from datetime import datetime, timezone

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "matches")


def _path(mid) -> str:
    return os.path.join(_DIR, f"{mid}.json")


def save_analysis(meta: dict, analysis: dict, message_id=None) -> dict:
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
