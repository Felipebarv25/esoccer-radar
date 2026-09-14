"""Score de FUERZA RELATIVA para un partido de eSoccer (jugador A vs B).

⚠️ ESTO NO ES UNA PROBABILIDAD NI UN PRONÓSTICO. Es un 0–100 que resume, con
datos factuales, hacia quién se inclina el partido:
  - 50  = parejo
  - >50 = se inclina al jugador A
  - <50 = se inclina al jugador B

Un número sacado de récords públicos NO predice quién gana ni tiene ventaja sobre
la casa. Sirve para leer el partido, no para apostar a ciegas.

Componentes (v1):
  - win_rate: diferencia de tasa de victoria de carrera.
  - h2h: quién domina el historial directo (partidos completados).
"""
import config
import team_ratings
from esb_source import career_summary

# Sin dato de equipo (o apagado): 60% carrera + 40% H2H (score original).
WEIGHTS = {"win_rate": 0.6, "h2h": 0.4}
# Con dato de equipo suficiente: se reparte para que el equipo mueva el score.
WEIGHTS_TEAM = {"win_rate": 0.4, "h2h": 0.3, "team": 0.3}
MIN_TEAM_GAMES = 5   # mínimo de partidos con ese equipo para que cuente


def h2h_record(source, nick_a: str, nick_b: str, max_pages: int = 3) -> dict:
    """Récord head-to-head de A vs B entre partidos COMPLETADOS (con marcador)."""
    a_win = b_win = draw = 0
    total_pages = 1
    page = 1
    while page <= min(max_pages, total_pages):
        data = source.compare_matches(nick_a, nick_b, page=page)
        total_pages = data.get("totalPages", 1)
        for m in data.get("matches", []):
            p1, p2 = m.get("participant1", {}), m.get("participant2", {})
            s1, s2 = p1.get("score"), p2.get("score")
            if s1 is None or s2 is None:
                continue  # partido futuro/sin jugar
            # ¿quién es A y quién B en este match? (por nickname)
            if p1.get("nickname") == nick_a:
                sa, sb = s1, s2
            else:
                sa, sb = s2, s1
            if sa > sb:
                a_win += 1
            elif sb > sa:
                b_win += 1
            else:
                draw += 1
        page += 1
    return {"a_win": a_win, "b_win": b_win, "draw": draw,
            "total": a_win + b_win + draw}


def h2h_stats(source, nick_a: str, nick_b: str, max_pages: int = 3) -> dict:
    """Récord + métricas de GOLES del head-to-head (partidos completados)."""
    a_win = b_win = draw = 0
    goals_total = a_goals = b_goals = 0
    over25 = over35 = over45 = btts = 0
    n = 0
    last5 = []
    total_pages = page = 1
    while page <= min(max_pages, total_pages):
        data = source.compare_matches(nick_a, nick_b, page=page)
        total_pages = data.get("totalPages", 1)
        for m in data.get("matches", []):
            p1, p2 = m.get("participant1", {}), m.get("participant2", {})
            s1, s2 = p1.get("score"), p2.get("score")
            if s1 is None or s2 is None:
                continue
            if p1.get("nickname") == nick_a:
                sa, sb = s1, s2
            else:
                sa, sb = s2, s1
            n += 1
            a_goals += sa; b_goals += sb
            tot = sa + sb
            goals_total += tot
            over25 += tot > 2.5; over35 += tot > 3.5; over45 += tot > 4.5
            btts += (sa > 0 and sb > 0)
            if sa > sb: a_win += 1
            elif sb > sa: b_win += 1
            else: draw += 1
            if len(last5) < 5:
                last5.append(f"{nick_a} {sa}-{sb} {nick_b} ({m.get('date','')[:10]})")
        page += 1
    def pct(x): return round(x / n, 2) if n else None
    return {
        "matches": n, "a_win": a_win, "b_win": b_win, "draw": draw,
        "avg_total_goals": round(goals_total / n, 2) if n else None,
        "a_goals_avg": round(a_goals / n, 2) if n else None,
        "b_goals_avg": round(b_goals / n, 2) if n else None,
        "over_2_5": pct(over25), "over_3_5": pct(over35), "over_4_5": pct(over45),
        "btts": pct(btts), "last5": last5,
    }


def analyze_match(source, nick_a: str, nick_b: str,
                  team_a: str = None, team_b: str = None) -> dict:
    """Análisis completo de un partido: carrera + forma reciente + H2H + score.

    team_a/team_b: los equipos que van a usar (para el componente de equipo del score).
    """
    comp = source.compare(nick_a, nick_b)
    pa = next((c for c in comp if c.get("nickname") == nick_a), comp[0])
    pb = next((c for c in comp if c.get("nickname") == nick_b), comp[-1])
    h2h = h2h_stats(source, nick_a, nick_b)
    strength = relative_strength(
        pa, pb, {"a_win": h2h["a_win"], "b_win": h2h["b_win"],
                 "draw": h2h["draw"], "total": h2h["matches"]},
        team_a=team_a, team_b=team_b, use_team=config.SCORE_USE_TEAM)
    form_a = source.recent_form(nick_a)
    form_b = source.recent_form(nick_b)
    return {
        "player_a": nick_a, "player_b": nick_b,
        "score_a": strength["score_a"], "favored": strength["favored"],
        "career": {"a": strength["player_a"], "b": strength["player_b"]},
        "recent_form": {"a": form_a, "b": form_b},
        "h2h": h2h,
        "team_detail": strength.get("team_detail"),
        "notes": strength["notes"],
    }


def _clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def relative_strength(part_a: dict, part_b: dict, h2h: dict,
                      team_a: str = None, team_b: str = None,
                      use_team: bool = True) -> dict:
    """Devuelve el score 0-100 (para el jugador A) + desglose. 50 = parejo."""
    ca, cb = career_summary(part_a), career_summary(part_b)
    notes = []

    # 1) win rate de carrera
    wr_a = ca["win_rate"] if ca["win_rate"] is not None else 0.5
    wr_b = cb["win_rate"] if cb["win_rate"] is not None else 0.5
    wr_signal = _clamp(wr_a - wr_b)  # -1..1

    # 2) head-to-head (partidos completados)
    if h2h["total"] > 0:
        h2h_signal = _clamp((h2h["a_win"] - h2h["b_win"]) / h2h["total"])
    else:
        h2h_signal = 0.0
        notes.append("sin head-to-head previo (peso 0)")

    # 3) EQUIPO: win% del jugador con el club que va a usar (si hay muestra)
    team_signal = None
    team_detail = None
    if use_team and team_a and team_b:
        ga, wa, wra = team_ratings.get(ca["nickname"], team_a)
        gb, wb, wrb = team_ratings.get(cb["nickname"], team_b)
        if ga >= MIN_TEAM_GAMES and gb >= MIN_TEAM_GAMES and wra is not None and wrb is not None:
            team_signal = _clamp(wra - wrb)
            team_detail = {"team_a": team_a, "wr_a": wra, "g_a": ga,
                           "team_b": team_b, "wr_b": wrb, "g_b": gb}
            notes.append(f"equipo: {team_a} {round(100 * wra)}% ({ga}) vs "
                         f"{team_b} {round(100 * wrb)}% ({gb})")

    if team_signal is not None:
        combined = (WEIGHTS_TEAM["win_rate"] * wr_signal
                    + WEIGHTS_TEAM["h2h"] * h2h_signal
                    + WEIGHTS_TEAM["team"] * team_signal)
    else:
        combined = WEIGHTS["win_rate"] * wr_signal + WEIGHTS["h2h"] * h2h_signal
    score_a = round(50 + combined * 50)  # 0..100, 50 = parejo

    favored = ca["nickname"] if score_a > 50 else (cb["nickname"] if score_a < 50 else "parejo")
    return {
        "score_a": score_a,
        "favored": favored,
        "player_a": ca, "player_b": cb,
        "h2h": h2h,
        "team_detail": team_detail,
        "components": {
            "win_rate": {"a": wr_a, "b": wr_b, "signal": round(wr_signal, 3)},
            "h2h": {"signal": round(h2h_signal, 3)},
            "team": ({"signal": round(team_signal, 3)} if team_signal is not None else None),
        },
        "notes": notes,
    }
