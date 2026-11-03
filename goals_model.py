"""Modelo de GOLES para el bot (#8) — entrena aparte, predice liviano.

Validamos (train_goals.py) que el total de goles SÍ es predecible y bien
calibrado en eSoccer. Este módulo lleva ese modelo al bot 24/7:

  - train_and_export(): entrena con scikit-learn y EXPORTA los coeficientes a un
    JSON chico (data/goals_model.json). Se corre aparte, de vez en cuando.
  - load() / GoalsPredictor.predict(): el BOT predice con pura aritmética
    (imputar + estandarizar + sigmoide), SIN importar scikit-learn, para no
    gastar RAM en la VM. Devuelve goles esperados + prob. calibrada de cada over.

Correr el export en el server (con el entorno activo):
    python goals_model.py              # usa el cache
    python goals_model.py --rebuild    # reconsolida desde los JSON crudos
"""
import json
import math
import os
import sys

import train_goals   # _FEATS, _eng, load_dataset (imports puros, sin sklearn)

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "goals_model.json")
_OVER_LINES = (4.5, 5.5, 6.5)   # líneas a exponer en la tarjeta (cerca del promedio)


# ---------------- ENTRENAMIENTO + EXPORT (offline, usa sklearn) ----------------
def train_and_export(rebuild: bool = False) -> dict:
    import numpy as np
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression, LinearRegression
    import dataset_cache

    cols, X, dates, totals, btts = train_goals.load_dataset(
        dataset_cache.iter_records(rebuild))
    n = X.shape[0]
    print(f"Entrenando modelo de goles con {n} partidos...", file=sys.stderr, flush=True)
    if n < 500:
        print("Muy pocos datos todavía. Deja acumular y reintenta.", file=sys.stderr)
        raise SystemExit(1)

    imp = SimpleImputer(strategy="median").fit(X)
    Xi = imp.transform(X)
    sc = StandardScaler().fit(Xi)
    Xs = sc.transform(Xi)

    overs = {}
    for T in _OVER_LINES:
        y = (totals > T).astype(int)
        lr = LogisticRegression(max_iter=1000).fit(Xs, y)
        overs[str(T)] = {"coef": [float(c) for c in lr.coef_[0]],
                         "intercept": float(lr.intercept_[0])}

    lin = LinearRegression().fit(Xs, totals)
    model = {
        "feats": cols,
        "median": [float(v) for v in imp.statistics_],
        "mean": [float(v) for v in sc.mean_],
        "scale": [float(v if v else 1.0) for v in sc.scale_],
        "overs": overs,
        "total": {"coef": [float(c) for c in lin.coef_], "intercept": float(lin.intercept_)},
        "trained_on": int(n),
        "avg_total": float(np.mean(totals)),
    }
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    with open(_PATH, "w", encoding="utf-8") as fh:
        json.dump(model, fh, ensure_ascii=False, indent=2)
    print(f"Listo: modelo exportado a {os.path.basename(_PATH)} "
          f"(prom. {model['avg_total']:.2f} goles).", file=sys.stderr, flush=True)
    return model


# ---------------- PREDICCIÓN (en el bot, pura aritmética) ----------------
class GoalsPredictor:
    def __init__(self, m: dict):
        self.feats = m["feats"]
        self.median = m["median"]
        self.mean = m["mean"]
        self.scale = m["scale"]
        self.overs = m["overs"]
        self.total = m["total"]
        self.avg_total = m.get("avg_total")

    def _z(self, features: dict):
        """Vector estandarizado del partido (imputa faltantes con la mediana)."""
        f = dict(features); f.update(train_goals._eng(features))
        z = []
        for j, c in enumerate(self.feats):
            v = f.get(c)
            if v is None or (isinstance(v, float) and math.isnan(v)):
                v = self.median[j]
            z.append((v - self.mean[j]) / self.scale[j])
        return z

    def predict(self, features: dict) -> dict:
        z = self._z(features)
        total = self.total["intercept"] + sum(c * zz for c, zz in zip(self.total["coef"], z))
        overs = {}
        for T, par in self.overs.items():
            lin = par["intercept"] + sum(c * zz for c, zz in zip(par["coef"], z))
            overs[float(T)] = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, lin))))
        return {"total": total, "overs": overs}


def load():
    """Devuelve un GoalsPredictor, o None si aún no se exportó el modelo."""
    if not os.path.exists(_PATH):
        return None
    try:
        with open(_PATH, "r", encoding="utf-8") as fh:
            return GoalsPredictor(json.load(fh))
    except (OSError, json.JSONDecodeError, KeyError):
        return None


if __name__ == "__main__":
    train_and_export(rebuild="--rebuild" in sys.argv)
