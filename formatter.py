"""Arma el mensaje de análisis de un partido para Telegram."""
import html
from datetime import datetime, timedelta, timezone

_COL = timezone(timedelta(hours=-5))  # Colombia = UTC-5 (sin horario de verano)


def _times(iso: str):
    """Devuelve (hora_colombia, hora_utc) desde una fecha ISO en UTC."""
    if not iso:
        return "", ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        utc = dt.astimezone(timezone.utc).strftime("%H:%M")
        col = dt.astimezone(_COL).strftime("%H:%M")
        return col, utc
    except Exception:
        return "", ""


def _pct(x):
    return f"{x:.0%}" if isinstance(x, (int, float)) else "—"


def _num(x):
    return f"{x}" if x is not None else "—"


def _reliability(h2h_matches, form_games_a, form_games_b) -> str:
    """Confiabilidad de la muestra: más partidos H2H/forma = más fiable."""
    if h2h_matches >= 15 and min(form_games_a, form_games_b) >= 6:
        return "🟢 muestra amplia"
    if h2h_matches >= 6:
        return "🟡 muestra media"
    return "🔴 muestra pequeña — tómalo con pinzas"


def format_match(meta: dict, a: dict, edges: list = None) -> str:
    """meta: {player1,team1,player2,team2,date}. a: analyze_match(...).

    `edges`: ventajas históricas detectadas (analytics.detect_edges); si hay,
    se muestran en un bloque 🔥 destacado arriba de la tarjeta.
    """
    A, B = a["player_a"], a["player_b"]
    ca, cb = a["career"]["a"], a["career"]["b"]
    fa, fb = a["recent_form"]["a"], a["recent_form"]["b"]
    h = a["h2h"]
    col, utc = _times(meta.get("date"))
    tipo = meta.get("match_type")  # 2x4 / 2x5 / 2x6 (si se conoce)

    rel = _reliability(h["matches"], fa.get("games", 0), fb.get("games", 0))

    # Score del FAVORITO: si score_a<50, el favorito es B y su score es 100-score_a.
    score_a = a["score_a"]
    if score_a > 50:
        fav, fav_score = A, score_a
    elif score_a < 50:
        fav, fav_score = B, 100 - score_a
    else:
        fav, fav_score = None, 50

    titulo = f"⚽ <b>eSoccer{f' ({tipo})' if tipo else ''} — {html.escape(A)} vs {html.escape(B)}</b>"
    reloj = ""
    if col:
        reloj = f"  · {col} COL ({utc} UTC)"
    if fav:
        linea_score = (f"📊 <b>Favorito: {html.escape(fav)} — Score {fav_score}/100</b>\n")
    else:
        linea_score = "📊 <b>Parejo (50/50)</b>\n"

    teams = f"🏳️ {html.escape(str(meta.get('team1','?')))} vs {html.escape(str(meta.get('team2','?')))}\n"
    cuerpo = (
        f"👤 Carrera win%: {A} {_pct(ca['win_rate'])} · {B} {_pct(cb['win_rate'])}\n"
        f"📈 Forma reciente (gol/partido): {A} {_num(fa['gf_per_game'])}⚽/{_num(fa['ga_per_game'])}🥅 · "
        f"{B} {_num(fb['gf_per_game'])}⚽/{_num(fb['ga_per_game'])}🥅\n"
        f"⚔️ H2H ({h['matches']}): {A} {h['a_win']}-{h['draw']}-{h['b_win']} {B}\n"
    )
    bloque_edge = ""
    if edges:
        bloque_edge = ("🚨 <b>ON FIRE</b> (según nuestros datos)\n"
                       + "\n".join(edges) + "\n\n")

    msg = titulo + reloj + "\n" + teams + "\n" + bloque_edge + linea_score + cuerpo
    if h["matches"]:
        msg += (
            f"⚽ Goles H2H: prom <b>{_num(h['avg_total_goals'])}</b> · "
            f"O2.5 {_pct(h['over_2_5'])} · O3.5 {_pct(h['over_3_5'])} · "
            f"O4.5 {_pct(h['over_4_5'])} · BTTS {_pct(h['btts'])}\n"
        )
        if h["last5"]:
            msg += "📋 " + " | ".join(x.split(" (")[0] for x in h["last5"][:3]) + "\n"
    msg += f"\n🔎 Confiabilidad: {rel}\n"
    msg += ("<i>Fuerza relativa + datos, NO probabilidad ni pronóstico. "
            "No apuestes a todo. Solo lo que puedas perder.</i>")
    return msg
