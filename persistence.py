"""Guarda cada análisis para backtesting (comparar score/métricas vs resultado real)."""
import glob
import json
import os
from datetime import datetime, timezone

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "matches")


def save_analysis(meta: dict, analysis: dict) -> str:
    os.makedirs(_DIR, exist_ok=True)
    mid = meta.get("match_id")
    rec = {
        "match_id": mid,
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "date": meta.get("date"),
        "player1": meta.get("player1"), "team1": meta.get("team1"),
        "player2": meta.get("player2"), "team2": meta.get("team2"),
        "score_a": analysis.get("score_a"),
        "favored": analysis.get("favored"),
        "analysis": analysis,
        "result": None,   # se completa después con el marcador real (backtesting)
    }
    path = os.path.join(_DIR, f"{mid}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=2)
    return path


def already_saved_ids() -> set:
    ids = set()
    for p in glob.glob(os.path.join(_DIR, "*.json")):
        ids.add(os.path.splitext(os.path.basename(p))[0])
    return ids
