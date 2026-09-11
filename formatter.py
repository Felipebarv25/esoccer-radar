"""Arma el mensaje de análisis de un partido para Telegram."""
import html


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


def format_match(meta: dict, a: dict) -> str:
    """meta: {player1,team1,player2,team2,date}. a: analyze_match(...)."""
    A, B = a["player_a"], a["player_b"]
    ca, cb = a["career"]["a"], a["career"]["b"]
    fa, fb = a["recent_form"]["a"], a["recent_form"]["b"]
    h = a["h2h"]
    hora = (meta.get("date") or "")[11:16]

    rel = _reliability(h["matches"], fa.get("games", 0), fb.get("games", 0))

    msg = (
        f"⚽ <b>eSoccer — {html.escape(A)} vs {html.escape(B)}</b>"
        + (f"  · {hora} UTC" if hora else "") + "\n"
        f"🏳️ {html.escape(str(meta.get('team1','?')))} vs {html.escape(str(meta.get('team2','?')))}\n"
        f"\n"
        f"📊 <b>Score {html.escape(A)}: {a['score_a']}/100</b>  (se inclina: {html.escape(str(a['favored']))})\n"
        f"👤 Carrera win%: {A} {_pct(ca['win_rate'])} · {B} {_pct(cb['win_rate'])}\n"
        f"📈 Forma reciente (gol/partido): {A} {_num(fa['gf_per_game'])}⚽/{_num(fa['ga_per_game'])}🥅 · "
        f"{B} {_num(fb['gf_per_game'])}⚽/{_num(fb['ga_per_game'])}🥅\n"
        f"⚔️ H2H ({h['matches']}): {A} {h['a_win']}-{h['draw']}-{h['b_win']} {B}\n"
    )
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
