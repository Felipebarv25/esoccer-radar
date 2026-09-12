"""Exporta el dataset para el modelo (#7): una fila por partido.

Cada fila = las FEATURES pre-partido (congeladas antes de jugar, sin leakage) +
las ETIQUETAS (labels) del resultado real. Lista para abrir en Excel/Weka/Knime
o cargar en pandas/sklearn.

Solo incluye partidos CERRADOS con marcador. Los registros viejos (antes del
snapshot ampliado) tendrán las columnas de features vacías; se llenan solo desde
que corre esta versión.
"""
import csv
import glob
import json
import os
from datetime import datetime, timezone

_MATCH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "matches")
_OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "reports")


def _rows():
    for p in glob.glob(os.path.join(_MATCH_DIR, "*.json")):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                rec = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        res = rec.get("result") or {}
        sa, sb = res.get("sa"), res.get("sb")
        if sa is None or sb is None:
            continue  # sin marcador → no sirve para el dataset
        total = sa + sb
        outcome = res.get("outcome")
        winner = res.get("winner")

        row = {
            "match_id": rec.get("match_id"), "date": rec.get("date"),
            "player1": rec.get("player1"), "player2": rec.get("player2"),
            "team1": rec.get("team1"), "team2": rec.get("team2"),
            "favored": rec.get("favored"),
        }
        row.update(rec.get("features") or {})   # todas las features pre-partido
        row.update({
            # etiquetas (targets posibles)
            "winner": winner, "sa": sa, "sb": sb, "total_goals": total,
            "over25": int(total >= 3), "over35": int(total >= 4),
            "over45": int(total >= 5), "btts": int(sa > 0 and sb > 0),
            "outcome": outcome,
            "fav_won": (1 if outcome == "acierto"
                        else (0 if outcome in ("fallo", "empate") else "")),
            "a_result": ("win" if winner == rec.get("player1")
                         else ("draw" if winner is None else "loss")),
        })
        yield row


def build_csv(path: str) -> int:
    rows = list(_rows())
    cols = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def generate_and_send(notifier) -> int:
    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(_OUT_DIR, f"dataset_{fecha}.csv")
    n = build_csv(path)
    if n == 0:
        notifier.send("🧠 Aún no hay partidos cerrados con features. Se llena a "
                      "medida que cierran partidos con el snapshot nuevo.")
        return 0
    con_feat = sum(1 for r in _rows() if r.get("elo_a") is not None)
    caption = (f"🧠 <b>Dataset para el modelo (#7)</b>\n"
               f"{n} partidos con resultado · {con_feat} ya con features completas "
               f"(Elo, forma, H2H...).\nUna fila por partido: features pre-partido "
               f"+ etiquetas (gana favorito, goles, Over, BTTS). Ábrelo en Excel o "
               f"cárgalo en pandas.\n<i>Base honesta y sin leakage para entrenar.</i>")
    notifier.send_document(path, caption)
    print(f"[REPORT] dataset enviado ({n} filas, {con_feat} con features)")
    return n


if __name__ == "__main__":
    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = os.path.join(_OUT_DIR, f"dataset_{fecha}.csv")
    n = build_csv(out)
    print(f"{n} filas → {out}")
