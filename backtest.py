"""Backtesting (#5a): cierra cada partido con su resultado real y responde a la alerta.

Cuando un partido ya debería haber terminado, busca el marcador, decide si la data
ACERTÓ al favorito (✅) o no (❌), responde bajo la alerta original y guarda el
desenlace para las tasas de acierto (#5b).
"""
import sys
from datetime import datetime, timedelta, timezone

import elo
import persistence
import team_ratings

END_BUFFER_MIN = 12    # esperar tras el inicio a que termine (partido + lag)
GIVEUP_MIN = 180       # si no aparece resultado, rendirse


def _parse(iso: str):
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except Exception:
        return None


def _format_close(rec: dict, result: dict, outcome: str) -> str:
    A, B = result["a"], result["b"]
    marcador = f"{A} {result['sa']}–{result['sb']} {B}"
    fav = rec.get("favored")
    tipo = rec.get("match_type")
    cab = f"eSoccer{f' ({tipo})' if tipo else ''}"
    if outcome == "acierto":
        top = f"✅ <b>ACERTÓ</b> — el favorito ({fav}) ganó"
    elif outcome == "fallo":
        top = f"❌ <b>Falló</b> — el favorito ({fav}) no ganó"
    elif outcome == "empate":
        top = "➖ <b>Empate</b> — sin ganador"
    else:  # parejo
        top = "⚪ <b>Cerrado</b> — el partido era parejo (sin favorito)"
    return f"{top}\n🏁 {cab}: {marcador}"


def process(source, notifier, pending: list) -> list:
    """Cierra los partidos que ya terminaron. Devuelve la lista aún pendiente."""
    now = datetime.now(timezone.utc)
    still = []
    for rec in pending:
        md = _parse(rec.get("date"))
        if md is None:
            continue  # sin fecha válida → descartar
        if now < md + timedelta(minutes=END_BUFFER_MIN):
            still.append(rec)
            continue  # aún no debería haber terminado
        result = None
        try:
            result = source.match_result(rec["player1"], rec["player2"], rec["match_id"])
        except Exception as e:
            print(f"[WARN] resultado {rec['match_id']}: {e}", file=sys.stderr)
        if result is None:
            if now > md + timedelta(minutes=GIVEUP_MIN):
                persistence.set_result(rec, {"outcome": "sin_datos",
                                             "closed_at": now.isoformat()})
                # Avisar bajo la alerta para que NUNCA quede en silencio.
                if rec.get("message_id"):
                    tipo = rec.get("match_type")
                    cab = f"eSoccer{f' ({tipo})' if tipo else ''}"
                    notifier.send(
                        f"⚠️ <b>Sin confirmar</b> — la fuente no entregó el "
                        f"marcador de este partido.\n🏁 {cab}: "
                        f"{rec['player1']} vs {rec['player2']} "
                        f"(no cuenta para la tasa de acierto).",
                        reply_to=rec["message_id"])
                print(f"[NODATA] {rec['player1']} vs {rec['player2']} → sin marcador",
                      file=sys.stderr)
            else:
                still.append(rec)
            continue

        fav = rec.get("favored")
        if fav is None:
            outcome, hit = "parejo", None
        elif result["winner"] is None:
            outcome, hit = "empate", None
        elif result["winner"] == fav:
            outcome, hit = "acierto", True
        else:
            outcome, hit = "fallo", False

        persistence.set_result(rec, {
            "score": f"{result['a']} {result['sa']}-{result['sb']} {result['b']}",
            "sa": result["sa"], "sb": result["sb"],   # marcador numérico (p1, p2)
            "winner": result["winner"], "favored": fav,
            "outcome": outcome, "hit": hit, "closed_at": now.isoformat(),
        })
        # Actualizar Elo de ambos jugadores (idempotente por match_id).
        try:
            elo.record(rec["player1"], rec["player2"], result["winner"],
                       match_id=rec["match_id"])
        except Exception as e:
            print(f"[WARN] elo.record {rec['match_id']}: {e}", file=sys.stderr)
        # Actualizar rendimiento por (jugador, equipo).
        try:
            team_ratings.record(rec["player1"], rec.get("team1"),
                                rec["player2"], rec.get("team2"),
                                result["winner"], match_id=rec["match_id"])
        except Exception as e:
            print(f"[WARN] team_ratings.record {rec['match_id']}: {e}", file=sys.stderr)
        if rec.get("message_id"):
            notifier.send(_format_close(rec, result, outcome), reply_to=rec["message_id"])
        print(f"[CLOSE] {rec['player1']} vs {rec['player2']} → {outcome} "
              f"({result['sa']}-{result['sb']})", file=sys.stderr)
    return still
