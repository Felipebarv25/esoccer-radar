"""Rating Elo por JUGADOR (Opción A) para eSoccer.

El Elo es un número de fuerza que sube/baja tras cada partido: ganarle al fuerte
sube mucho; perder contra el débil baja mucho. Es auto-corrector.

  - Esperado de A:  E_A = 1 / (1 + 10^((R_B - R_A)/400))   → ya es una probabilidad
  - Actualización:  R_A' = R_A + K·(S_A - E_A)   (S: 1 gana, 0.5 empata, 0 pierde)

Estado en data/elo_state.json: {ratings:{nick:{rating,games}}, done:[match_ids]}.
`done` hace la actualización IDEMPOTENTE: un mismo partido nunca cuenta dos veces
(así el bootstrap histórico y la actualización en vivo no se pisan).

HONESTO: es un modelo de fuerza, no una bola de cristal. En eSoccer (alta
varianza, posibles amaños) será más ruidoso que en ajedrez. Buen feature, no oráculo.
"""
import json
import os
import tempfile

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_FILE = os.path.join(_DIR, "elo_state.json")

INITIAL = 1500.0
DRAW = 0.5
K_BASE = 24            # velocidad normal
K_PROVISIONAL = 40     # primeros partidos: aprende más rápido
PROVISIONAL_GAMES = 30

_state = None


def _load():
    global _state
    if _state is None:
        try:
            with open(_FILE, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (json.JSONDecodeError, OSError):
            raw = {}
        _state = {"ratings": raw.get("ratings", {}),
                  "done_set": set(raw.get("done", []))}
    return _state


def _save():
    s = _load()
    data = {"ratings": s["ratings"], "done": list(s["done_set"])}
    os.makedirs(_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, _FILE)   # escritura atómica
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def get(nick):
    """(rating, games) del jugador. Nuevo → (1500, 0)."""
    r = _load()["ratings"].get(nick)
    return (r["rating"], r["games"]) if r else (INITIAL, 0)


def expected(ra, rb):
    """Probabilidad esperada de que gane A según sus Elos."""
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def _k(games):
    return K_PROVISIONAL if games < PROVISIONAL_GAMES else K_BASE


def snapshot(nick_a, nick_b):
    """Foto PRE-partido (para la tarjeta y el dataset, sin modificar nada)."""
    ra, ga = get(nick_a)
    rb, gb = get(nick_b)
    return {"elo_a": round(ra), "elo_b": round(rb),
            "exp_a": round(expected(ra, rb), 3),
            "games_a": ga, "games_b": gb}


def record(nick_a, nick_b, winner, match_id=None, save=True):
    """Actualiza el Elo de ambos tras un partido. Idempotente por match_id.

    save=False no escribe a disco (para lotes grandes: aplica en memoria y luego
    llama flush() una sola vez). En vivo se deja save=True.
    """
    s = _load()
    mid = None if match_id is None else str(match_id)
    if mid is not None and mid in s["done_set"]:
        return False  # ya contado
    ra, ga = get(nick_a)
    rb, gb = get(nick_b)
    if winner == nick_a:
        sa = 1.0
    elif winner == nick_b:
        sa = 0.0
    else:
        sa = DRAW
    ea = expected(ra, rb)
    ra2 = ra + _k(ga) * (sa - ea)
    rb2 = rb + _k(gb) * ((1 - sa) - (1 - ea))
    s["ratings"][nick_a] = {"rating": round(ra2, 2), "games": ga + 1}
    s["ratings"][nick_b] = {"rating": round(rb2, 2), "games": gb + 1}
    if mid is not None:
        s["done_set"].add(mid)
    if save:
        _save()
    return True


def flush():
    """Escribe el estado a disco (para usar tras un lote con save=False)."""
    _save()


def top(n=20, min_games=5):
    """Ranking por Elo (jugadores con al menos min_games partidos)."""
    rows = [(nick, r["rating"], r["games"])
            for nick, r in _load()["ratings"].items() if r["games"] >= min_games]
    rows.sort(key=lambda x: x[1], reverse=True)
    return rows[:n]
