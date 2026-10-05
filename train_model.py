"""Entrena y VALIDA un modelo ML para eSoccer (#7), honesto y comparado al Elo.

Pregunta clave: ¿un modelo con TODAS las features (carrera, forma, H2H, Elo,
equipo, hora...) predice MEJOR que el Elo solo? Si no, el Elo ya es el techo.

- Target: P(gana el jugador 1) — y=1 si ganó el 1, 0 si empató o ganó el 2
  (modela una apuesta a que gane 1, donde empate = no gana).
- Split TEMPORAL (entrena con lo viejo, prueba con lo reciente) → sin leakage.
- Modelos: regresión logística (baseline) y Random Forest.
- Métricas en test: Brier, log-loss, AUC, accuracy + CALIBRACIÓN por banda.
- Baseline de referencia: la probabilidad del Elo (elo_exp_a) ya guardada.

Uso en el server:
    source .venv/bin/activate
    pip install scikit-learn
    python train_model.py
"""
import glob
import json
import os
import sys

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "matches")

# features numéricas que guarda persistence.build_features
_FEATS = [
    "score_a", "hour_col", "weekday",
    "career_wr_a", "career_wr_b", "career_matches_a", "career_matches_b",
    "form_gf_a", "form_ga_a", "form_wr_a", "form_games_a",
    "form_gf_b", "form_ga_b", "form_wr_b", "form_games_b",
    "h2h_matches", "h2h_a_win", "h2h_draw", "h2h_b_win", "h2h_avg_goals",
    "h2h_over25", "h2h_over35", "h2h_over45", "h2h_btts",
    "elo_a", "elo_b", "elo_exp_a", "elo_games_a", "elo_games_b",
    "team_wr_a", "team_wr_b",
]


def _diffs(f):
    """Diferencias A-B (ayudan al modelo lineal)."""
    def d(a, b):
        x, y = f.get(a), f.get(b)
        return (x - y) if (x is not None and y is not None) else None
    return {
        "elo_diff": d("elo_a", "elo_b"),
        "career_wr_diff": d("career_wr_a", "career_wr_b"),
        "form_wr_diff": d("form_wr_a", "form_wr_b"),
        "team_wr_diff": d("team_wr_a", "team_wr_b"),
        "h2h_diff": d("h2h_a_win", "h2h_b_win"),
    }


def load_dataset():
    rows, ys, dates, elo_base = [], [], [], []
    cols = _FEATS + ["elo_diff", "career_wr_diff", "form_wr_diff", "team_wr_diff", "h2h_diff"]
    for p in glob.glob(os.path.join(_DIR, "*.json")):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                rec = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        feat = rec.get("features") or {}
        res = rec.get("result") or {}
        if not feat or feat.get("elo_a") is None:
            continue   # solo registros CON snapshot de features
        if res.get("outcome") not in ("acierto", "fallo", "empate"):
            continue   # cerrado y decidible
        winner = res.get("winner")
        y = 1 if winner == rec.get("player1") else 0
        f = dict(feat); f.update(_diffs(feat))
        rows.append([f.get(c) for c in cols])
        ys.append(y)
        dates.append(rec.get("date") or "")
        elo_base.append(feat.get("elo_exp_a"))
    return cols, rows, ys, dates, elo_base


def _report(name, y_true, p):
    import numpy as np
    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score, accuracy_score
    p = np.clip(np.array(p, dtype=float), 1e-6, 1 - 1e-6)
    y = np.array(y_true)
    acc = accuracy_score(y, (p >= 0.5).astype(int))
    print(f"\n  [{name}]  Brier {brier_score_loss(y, p):.4f} · "
          f"logloss {log_loss(y, p):.4f} · AUC {roc_auc_score(y, p):.3f} · "
          f"acc {acc:.3f}")
    # calibración por banda de prob. del favorito
    print("    calibración (prob→real):", end=" ")
    for lo in (0.5, 0.6, 0.7, 0.8):
        hi = lo + 0.1
        favp = np.where(p >= 0.5, p, 1 - p)
        favwin = np.where(p >= 0.5, y, 1 - y)
        m = (favp >= lo) & (favp < hi + (0.11 if lo == 0.8 else 0))
        if m.sum() >= 10:
            print(f"{int(lo*100)}-{int(hi*100)}:{round(100*favwin[m].mean())}%(n={int(m.sum())})", end="  ")
    print()


def main():
    try:
        import numpy as np
        from sklearn.impute import SimpleImputer
        from sklearn.preprocessing import StandardScaler
        from sklearn.linear_model import LogisticRegression
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.pipeline import make_pipeline
    except ImportError:
        print("Falta scikit-learn. Instálalo:  pip install scikit-learn", file=sys.stderr)
        raise SystemExit(1)

    cols, rows, ys, dates, elo_base = load_dataset()
    n = len(rows)
    print(f"Dataset: {n} partidos con features + resultado.")
    if n < 500:
        print("Muy pocos datos con features todavía (necesitamos ~1000+). "
              "Deja acumular y reintenta.")
        return

    order = sorted(range(n), key=lambda i: dates[i])
    rows = [rows[i] for i in order]; ys = [ys[i] for i in order]
    elo_base = [elo_base[i] for i in order]
    cut = int(n * 0.8)
    Xtr, Xte = rows[:cut], rows[cut:]
    ytr, yte = ys[:cut], ys[cut:]
    elo_te = elo_base[cut:]
    print(f"Train {len(Xtr)} · Test {len(Xte)} (split temporal 80/20)")
    print(f"Tasa base: el jugador 1 gana {round(100*sum(yte)/len(yte))}% en test")

    # baseline: la probabilidad del Elo
    eb = [(e if e is not None else 0.5) for e in elo_te]
    _report("BASELINE Elo (elo_exp_a)", yte, eb)

    # modelo 1: regresión logística
    logit = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                          LogisticRegression(max_iter=1000, C=1.0))
    logit.fit(Xtr, ytr)
    _report("Regresión logística", yte, logit.predict_proba(Xte)[:, 1])

    # modelo 2: Random Forest
    rf = make_pipeline(SimpleImputer(strategy="median"),
                       RandomForestClassifier(n_estimators=300, max_depth=8,
                                              min_samples_leaf=20, n_jobs=-1,
                                              random_state=42))
    rf.fit(Xtr, ytr)
    _report("Random Forest", yte, rf.predict_proba(Xte)[:, 1])

    # importancia de variables (RF)
    imp = rf.steps[-1][1].feature_importances_
    top = sorted(zip(cols, imp), key=lambda x: x[1], reverse=True)[:12]
    print("\n  Variables más importantes (Random Forest):")
    for c, v in top:
        print(f"    {v:.3f}  {c}")

    print("\n  LECTURA: si el Brier/logloss del modelo es MENOR que el del Elo, el "
          "modelo aporta. Si son casi iguales, el Elo ya es el techo.")


if __name__ == "__main__":
    main()
