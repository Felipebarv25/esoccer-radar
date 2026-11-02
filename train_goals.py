"""Entrena y VALIDA modelos ML de GOLES para eSoccer (#8), honesto y comparado.

Hermano de train_model.py (que modela el RESULTADO). Aquí atacamos las variables
que el usuario pidió: cantidad de goles. Lo honesto primero:

  La fuente (esportsbattle) SOLO publica el MARCADOR. No hay córners, ni tiros, ni
  posesión, ni tarjetas en ningún endpoint — así que esas NO se pueden modelar.
  Lo que SÍ sale del marcador + la foto pre-partido (leak-free):
    - Total de goles  → Over/Under 2.5 / 3.5 / 4.5  (clasificación)
    - Total de goles  → regresión (¿predecimos el número?)
    - BTTS            → ambos marcan (clasificación)

La pregunta de negocio: ¿un modelo predice el total MEJOR que la tasa base / que la
heurística de "goles esperados"? Si no, el mercado de totales no es batible con
nuestros datos (igual que concluimos en tenis de mesa).

- Split TEMPORAL (entrena viejo, prueba reciente) → sin leakage.
- Clasificación: Brier/logloss/AUC + CALIBRACIÓN por banda vs tasa base.
- Regresión: MAE + correlación vs (media constante | goles esperados | h2h).

Uso en el server:
    source .venv/bin/activate
    pip install scikit-learn
    python train_goals.py
"""
import glob
import json
import os
import sys

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "matches")

# features numéricas pre-partido relevantes para GOLES (de persistence.build_features)
_FEATS = [
    "is_2x6", "hour_col", "weekday",
    "career_matches_a", "career_matches_b",
    "form_gf_a", "form_ga_a", "form_wr_a", "form_games_a",
    "form_gf_b", "form_ga_b", "form_wr_b", "form_games_b",
    "h2h_matches", "h2h_avg_goals",
    "h2h_over25", "h2h_over35", "h2h_over45", "h2h_btts",
    "elo_a", "elo_b",
    # ingeniería: goles esperados (ataque propio + defensa rival)
    "exp_goals_a", "exp_goals_b", "exp_total",
]


def _eng(f):
    """Variables derivadas: estimación de goles esperados por la forma reciente."""
    gfa, gab = f.get("form_gf_a"), f.get("form_ga_b")
    gfb, gaa = f.get("form_gf_b"), f.get("form_ga_a")
    ega = (gfa + gab) / 2 if (gfa is not None and gab is not None) else None
    egb = (gfb + gaa) / 2 if (gfb is not None and gaa is not None) else None
    et = (ega + egb) if (ega is not None and egb is not None) else None
    return {"is_2x6": 1 if f.get("match_type") == "2x6" else 0,
            "exp_goals_a": ega, "exp_goals_b": egb, "exp_total": et}


def load_dataset():
    rows, dates = [], []
    totals, over25, over35, over45, btts = [], [], [], [], []
    exp_tot, h2h_avg = [], []
    files = glob.glob(os.path.join(_DIR, "*.json"))
    print(f"Leyendo {len(files)} archivos de data/matches...", file=sys.stderr, flush=True)
    for i, p in enumerate(files, 1):
        if i % 2000 == 0:
            print(f"  ...{i}/{len(files)}", file=sys.stderr, flush=True)
        try:
            with open(p, "r", encoding="utf-8") as fh:
                rec = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        feat = rec.get("features") or {}
        res = rec.get("result") or {}
        if not feat:
            continue
        sa, sb = res.get("sa"), res.get("sb")
        if sa is None or sb is None:   # solo partidos cerrados CON marcador
            continue
        f = dict(feat); f.update(_eng(feat))
        rows.append([f.get(c) for c in _FEATS])
        tot = sa + sb
        totals.append(tot)
        over25.append(1 if tot >= 3 else 0)
        over35.append(1 if tot >= 4 else 0)
        over45.append(1 if tot >= 5 else 0)
        btts.append(1 if (sa > 0 and sb > 0) else 0)
        exp_tot.append(f.get("exp_total"))
        h2h_avg.append(f.get("h2h_avg_goals"))
        dates.append(rec.get("date") or "")
    return (_FEATS, rows, dates, totals, over25, over35, over45, btts,
            exp_tot, h2h_avg)


def _clf_report(name, y_true, p):
    import numpy as np
    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score, accuracy_score
    p = np.clip(np.array(p, dtype=float), 1e-6, 1 - 1e-6)
    y = np.array(y_true)
    try:
        auc = roc_auc_score(y, p)
    except ValueError:
        auc = float("nan")
    acc = accuracy_score(y, (p >= 0.5).astype(int))
    print(f"    [{name}]  Brier {brier_score_loss(y, p):.4f} · "
          f"logloss {log_loss(y, p, labels=[0, 1]):.4f} · AUC {auc:.3f} · acc {acc:.3f}")


def _calib(y_true, p):
    """Calibración por banda: ¿la prob. del modelo coincide con el real?"""
    import numpy as np
    p = np.array(p, dtype=float); y = np.array(y_true)
    print("      calibración (prob→real):", end=" ")
    for lo in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8):
        hi = lo + 0.1
        m = (p >= lo) & (p < hi)
        if m.sum() >= 15:
            print(f"{int(lo*100)}-{int(hi*100)}:{round(100*y[m].mean())}%(n={int(m.sum())})",
                  end="  ")
    print()


def _run_clf(label, Xtr, Xte, ytr, yte):
    import numpy as np
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.pipeline import make_pipeline

    base = sum(ytr) / len(ytr)          # tasa base en TRAIN
    print(f"\n  === {label} ===  (tasa base train {round(100*base)}% · "
          f"real test {round(100*sum(yte)/len(yte))}%)")
    _clf_report("BASELINE tasa base", yte, [base] * len(yte))

    logit = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                          LogisticRegression(max_iter=1000))
    logit.fit(Xtr, ytr)
    pl = logit.predict_proba(Xte)[:, 1]
    _clf_report("Regresión logística", yte, pl)

    rf = make_pipeline(SimpleImputer(strategy="median"),
                       RandomForestClassifier(n_estimators=300, max_depth=8,
                                              min_samples_leaf=20, n_jobs=-1,
                                              random_state=42))
    rf.fit(Xtr, ytr)
    pr = rf.predict_proba(Xte)[:, 1]
    _clf_report("Random Forest", yte, pr)
    _calib(yte, pr)
    return rf


def _reg_report(name, y_true, pred):
    import numpy as np
    y = np.array(y_true, dtype=float); p = np.array(pred, dtype=float)
    mae = np.mean(np.abs(y - p))
    rmse = np.sqrt(np.mean((y - p) ** 2))
    print(f"    [{name}]  MAE {mae:.3f} · RMSE {rmse:.3f}")
    return mae


def main():
    try:
        import numpy as np
        from sklearn.impute import SimpleImputer
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.pipeline import make_pipeline
    except ImportError:
        print("Falta scikit-learn. Instálalo:  pip install scikit-learn", file=sys.stderr)
        raise SystemExit(1)

    (cols, rows, dates, totals, over25, over35, over45, btts,
     exp_tot, h2h_avg) = load_dataset()
    n = len(rows)
    print(f"Dataset: {n} partidos cerrados con marcador + features.")
    if n < 500:
        print("Muy pocos datos todavía (necesitamos ~1000+). Deja acumular y reintenta.")
        return

    order = sorted(range(n), key=lambda i: dates[i])
    def srt(a): return [a[i] for i in order]
    rows = srt(rows); totals = srt(totals)
    over25 = srt(over25); over35 = srt(over35); over45 = srt(over45); btts = srt(btts)
    exp_tot = srt(exp_tot); h2h_avg = srt(h2h_avg)

    cut = int(n * 0.8)
    Xtr, Xte = rows[:cut], rows[cut:]
    print(f"Train {len(Xtr)} · Test {len(Xte)} (split temporal 80/20)")
    print(f"Promedio de goles por partido (test): {np.mean(totals[cut:]):.2f}")

    # ---------- CLASIFICACIÓN: overs + BTTS ----------
    for lbl, y in (("OVER 2.5 goles", over25), ("OVER 3.5 goles", over35),
                   ("OVER 4.5 goles", over45), ("BTTS (ambos marcan)", btts)):
        _run_clf(lbl, Xtr, Xte, y[:cut], y[cut:])

    # ---------- REGRESIÓN: total de goles ----------
    print("\n  === TOTAL DE GOLES (regresión) ===")
    ytr, yte = totals[:cut], totals[cut:]
    _reg_report("BASELINE media constante", yte, [np.mean(ytr)] * len(yte))
    # heurística: goles esperados por la forma
    et_te = [e if e is not None else np.mean(ytr) for e in exp_tot[cut:]]
    _reg_report("Heurística goles esperados", yte, et_te)
    ha_te = [h if h is not None else np.mean(ytr) for h in h2h_avg[cut:]]
    _reg_report("Heurística h2h_avg_goals", yte, ha_te)
    reg = make_pipeline(SimpleImputer(strategy="median"),
                        RandomForestRegressor(n_estimators=300, max_depth=8,
                                              min_samples_leaf=20, n_jobs=-1,
                                              random_state=42))
    reg.fit(Xtr, ytr)
    pred = reg.predict(Xte)
    mae_model = _reg_report("Random Forest (modelo)", yte, pred)
    corr = np.corrcoef(yte, pred)[0, 1]
    print(f"    correlación modelo vs real: {corr:.3f}")

    imp = reg.steps[-1][1].feature_importances_
    top = sorted(zip(cols, imp), key=lambda x: x[1], reverse=True)[:10]
    print("\n  Variables más importantes para el total (RF):")
    for c, v in top:
        print(f"    {v:.3f}  {c}")

    print("\n  LECTURA HONESTA:")
    print("  - Si el Brier del modelo < Brier de la tasa base → el modelo aporta en overs.")
    print("  - Si el MAE del modelo ≈ MAE de la media constante → el total NO es predecible")
    print("    (el mercado de más/menos goles no es batible con estos datos).")
    print("  - La CALIBRACIÓN dice si las probabilidades de over son usables o humo.")


if __name__ == "__main__":
    main()
