"""Entrena y VALIDA modelos ML de GOLES para eSoccer (#8), honesto y RÁPIDO.

Hermano de train_model.py (que modela el RESULTADO). Aquí atacamos lo que el
usuario pidió: cantidad de goles. Lo honesto primero:

  La fuente (esportsbattle) SOLO publica el MARCADOR. No hay córners, ni tiros, ni
  posesión, ni tarjetas en ningún endpoint — así que esas NO se pueden modelar.
  Lo que SÍ sale del marcador + la foto pre-partido (leak-free):
    - Total de goles  → Over/Under en varios umbrales  (clasificación)
    - Total de goles  → regresión (¿predecimos el número?)
    - BTTS            → ambos marcan (clasificación)

Como el promedio ronda 6 goles, los umbrales útiles están CERCA del promedio
(5.5, 6.5, 7.5). El Over 2.5 es casi siempre sí (no hay mercado).

Usa modelos RÁPIDOS (logística + gradient boosting por histograma), sin Random
Forest, para que corra en segundos en la VM. La pregunta de negocio se responde
igual: ¿un modelo predice MEJOR que la tasa base / que la heurística de goles?

- Split TEMPORAL (entrena viejo, prueba reciente) → sin leakage.
- Clasificación: Brier/logloss/AUC + CALIBRACIÓN por banda vs tasa base.
- Regresión: MAE vs (media constante | goles esperados | h2h | modelo).
- Correlaciones: qué variable se asocia de verdad con el total de goles.

Uso en el server:
    source .venv/bin/activate
    python train_goals.py            # usa el cache si existe (segundos)
    python train_goals.py --rebuild  # reconsolida desde los JSON crudos
"""
import sys

import dataset_cache

_FEATS = [
    "is_2x6", "hour_col", "weekday",
    "career_matches_a", "career_matches_b",
    "form_gf_a", "form_ga_a", "form_wr_a", "form_games_a",
    "form_gf_b", "form_ga_b", "form_wr_b", "form_games_b",
    "h2h_matches", "h2h_avg_goals",
    "h2h_over25", "h2h_over35", "h2h_over45", "h2h_btts",
    "elo_a", "elo_b",
    "exp_goals_a", "exp_goals_b", "exp_total",
]

_THRESHOLDS = (2.5, 4.5, 5.5, 6.5, 7.5)   # umbrales de over a evaluar


def _eng(f):
    """Variables derivadas: estimación de goles esperados por la forma reciente."""
    gfa, gab = f.get("form_gf_a"), f.get("form_ga_b")
    gfb, gaa = f.get("form_gf_b"), f.get("form_ga_a")
    ega = (gfa + gab) / 2 if (gfa is not None and gab is not None) else None
    egb = (gfb + gaa) / 2 if (gfb is not None and gaa is not None) else None
    et = (ega + egb) if (ega is not None and egb is not None) else None
    return {"is_2x6": 1 if f.get("match_type") == "2x6" else 0,
            "exp_goals_a": ega, "exp_goals_b": egb, "exp_total": et}


def load_dataset(records):
    rows, dates, totals, btts, exp_tot, h2h_avg = [], [], [], [], [], []
    for rec in records:
        feat = rec.get("features") or {}
        res = rec.get("result") or {}
        if not feat:
            continue
        sa, sb = res.get("sa"), res.get("sb")
        if sa is None or sb is None:   # solo partidos cerrados CON marcador
            continue
        f = dict(feat); f.update(_eng(feat))
        rows.append([f.get(c) for c in _FEATS])
        totals.append(sa + sb)
        btts.append(1 if (sa > 0 and sb > 0) else 0)
        exp_tot.append(f.get("exp_total"))
        h2h_avg.append(f.get("h2h_avg_goals"))
        dates.append(rec.get("date") or "")
    return _FEATS, rows, dates, totals, btts, exp_tot, h2h_avg


def _clf_report(name, y_true, p):
    import numpy as np
    from sklearn.metrics import brier_score_loss, roc_auc_score, accuracy_score
    p = np.clip(np.array(p, dtype=float), 1e-6, 1 - 1e-6)
    y = np.array(y_true)
    try:
        auc = roc_auc_score(y, p)
    except ValueError:
        auc = float("nan")
    acc = accuracy_score(y, (p >= 0.5).astype(int))
    print(f"    [{name}]  Brier {brier_score_loss(y, p):.4f} · "
          f"AUC {auc:.3f} · acc {acc:.3f}")


def _calib(y_true, p):
    """Calibración por banda: ¿la prob. del modelo coincide con el real?"""
    import numpy as np
    p = np.array(p, dtype=float); y = np.array(y_true)
    print("      calibración (prob→real):", end=" ")
    for lo in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        hi = lo + 0.1
        m = (p >= lo) & (p < hi)
        if m.sum() >= 15:
            print(f"{int(lo*100)}-{int(hi*100)}:{round(100*y[m].mean())}%(n={int(m.sum())})",
                  end="  ")
    print()


def _run_clf(label, model_fn, Xtr, Xte, ytr, yte):
    import numpy as np
    base = sum(ytr) / len(ytr)          # tasa base en TRAIN
    if base in (0.0, 1.0):
        print(f"\n  === {label} ===  (tasa base {round(100*base)}% — sin mercado, se omite)")
        return
    print(f"\n  === {label} ===  (tasa base train {round(100*base)}% · "
          f"real test {round(100*sum(yte)/len(yte))}%)")
    _clf_report("BASELINE tasa base", yte, [base] * len(yte))
    model = model_fn()
    model.fit(Xtr, ytr)
    p = model.predict_proba(Xte)[:, 1]
    _clf_report("Modelo (logística)", yte, p)
    _calib(yte, p)


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
        from sklearn.preprocessing import StandardScaler
        from sklearn.linear_model import LogisticRegression
        from sklearn.ensemble import HistGradientBoostingRegressor
        from sklearn.pipeline import make_pipeline
    except ImportError:
        print("Falta scikit-learn. Instálalo:  pip install scikit-learn", file=sys.stderr)
        raise SystemExit(1)

    rebuild = "--rebuild" in sys.argv
    records = dataset_cache.load_records(rebuild=rebuild)
    cols, rows, dates, totals, btts, exp_tot, h2h_avg = load_dataset(records)
    n = len(rows)
    print(f"Dataset: {n} partidos cerrados con marcador + features.")
    if n < 500:
        print("Muy pocos datos todavía (necesitamos ~1000+). Deja acumular y reintenta.")
        return

    order = sorted(range(n), key=lambda i: dates[i])
    def srt(a): return [a[i] for i in order]
    rows = srt(rows); totals = srt(totals); btts = srt(btts)
    exp_tot = srt(exp_tot); h2h_avg = srt(h2h_avg)

    cut = int(n * 0.8)
    Xtr, Xte = rows[:cut], rows[cut:]
    print(f"Train {len(Xtr)} · Test {len(Xte)} (split temporal 80/20)")
    print(f"Promedio de goles por partido (test): {np.mean(totals[cut:]):.2f}")

    def logit():
        return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                             LogisticRegression(max_iter=1000))

    # ---------- CLASIFICACIÓN: overs a varios umbrales + BTTS (rápido) ----------
    for T in _THRESHOLDS:
        y = [1 if t > T else 0 for t in totals]
        _run_clf(f"OVER {T} goles", logit, Xtr, Xte, y[:cut], y[cut:])
    _run_clf("BTTS (ambos marcan)", logit, Xtr, Xte, btts[:cut], btts[cut:])

    # ---------- REGRESIÓN: total de goles ----------
    print("\n  === TOTAL DE GOLES (regresión) ===")
    ytr, yte = totals[:cut], totals[cut:]
    _reg_report("BASELINE media constante", yte, [np.mean(ytr)] * len(yte))
    et_te = [e if e is not None else np.mean(ytr) for e in exp_tot[cut:]]
    _reg_report("Heurística goles esperados", yte, et_te)
    ha_te = [h if h is not None else np.mean(ytr) for h in h2h_avg[cut:]]
    _reg_report("Heurística h2h_avg_goals", yte, ha_te)
    print("  entrenando modelo (gradient boosting)...", file=sys.stderr, flush=True)
    reg = HistGradientBoostingRegressor(max_depth=5, max_iter=200, random_state=42)
    reg.fit([[(v if v is not None else np.nan) for v in r] for r in Xtr], ytr)
    pred = reg.predict([[(v if v is not None else np.nan) for v in r] for r in Xte])
    _reg_report("Modelo gradient boosting", yte, pred)
    corr = np.corrcoef(yte, pred)[0, 1]
    print(f"    correlación modelo vs real: {corr:.3f}")

    # ---------- CORRELACIONES: qué variable se asocia con el total ----------
    print("\n  Correlación de cada variable con el TOTAL de goles (test):")
    Xte_arr = np.array([[(v if v is not None else np.nan) for v in r] for r in Xte],
                       dtype=float)
    yv = np.array(yte, dtype=float)
    cors = []
    for j, name in enumerate(cols):
        col = Xte_arr[:, j]
        mask = ~np.isnan(col)
        if mask.sum() > 100 and np.std(col[mask]) > 0:
            cors.append((name, np.corrcoef(col[mask], yv[mask])[0, 1]))
    for name, c in sorted(cors, key=lambda x: abs(x[1]), reverse=True)[:10]:
        print(f"    {c:+.3f}  {name}")

    print("\n  LECTURA HONESTA:")
    print("  - Si el Brier del modelo < Brier de la tasa base → el modelo aporta en overs.")
    print("  - Si el MAE del modelo ≈ MAE de la media constante → el total NO es predecible")
    print("    (el mercado de más/menos goles no es batible con estos datos).")
    print("  - Las correlaciones dicen qué variable (si alguna) mueve el total de goles.")


if __name__ == "__main__":
    main()
