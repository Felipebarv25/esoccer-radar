"""eSoccer Radar — loop 24/7.

Consulta los próximos partidos, analiza cada uno UNA sola vez (antes de empezar),
publica el análisis en el canal de Telegram y lo guarda para backtesting.

Uso:
    python run_esoccer.py
    python run_esoccer.py --no-startup
"""
import argparse
import sys
import time

import backtest
import config
import persistence
import reports
from esb_source import ESBSource, match_pairs
from esb_score import analyze_match
from formatter import format_match
from telegram_notifier import TelegramNotifier


def main():
    parser = argparse.ArgumentParser(description="eSoccer Radar 24/7")
    parser.add_argument("--no-startup", action="store_true")
    args = parser.parse_args()

    config.setup_console()
    config.validate()

    source = ESBSource()
    notifier = TelegramNotifier()
    seen = persistence.already_saved_ids()   # no re-avisar los ya guardados
    pending = persistence.load_open()        # partidos alertados sin desenlace aún

    interval = config.POLL_INTERVAL_SECONDS
    print(f"eSoccer Radar — intervalo {interval}s · ya guardados: {len(seen)}")
    if not args.no_startup:
        notifier.send("🟢 <b>eSoccer Radar activo</b>\nAnalizando próximos partidos de "
                      "eSports Battle. <i>Datos, NO pronósticos.</i>")

    while True:
        try:
            pairs = match_pairs(source.upcoming_matches())
        except Exception as e:
            print(f"[WARN] fallo al listar próximos: {e}", file=sys.stderr)
            time.sleep(interval)
            continue

        nuevos = 0
        for p in pairs:
            mid = str(p.get("match_id"))
            if mid in seen or not p.get("player1") or not p.get("player2"):
                continue
            seen.add(mid)
            try:
                a = analyze_match(source, p["player1"], p["player2"])
            except Exception as e:
                print(f"[WARN] no pude analizar {p['player1']} vs {p['player2']}: {e}",
                      file=sys.stderr)
                continue
            msg_id = notifier.send(format_match(p, a))   # enviar primero → obtener message_id
            rec = persistence.save_analysis(p, a, message_id=msg_id)
            pending.append(rec)
            nuevos += 1
            print(f"[MATCH] {p['player1']} vs {p['player2']} → score {a['score_a']} "
                  f"| Telegram {'OK' if msg_id else 'FALLÓ'}", file=sys.stderr)

        # Cerrar los partidos que ya terminaron (backtesting: ✅/❌ bajo la alerta).
        try:
            pending = backtest.process(source, notifier, pending)
        except Exception as e:
            print(f"[WARN] backtest.process: {e}", file=sys.stderr)

        # Reportes programados de tasa de acierto (diario/semanal/quincenal/mensual).
        try:
            reports.maybe_send(notifier)
            reports.maybe_team_report(notifier)  # CSV jugador-equipo cada 2 días
        except Exception as e:
            print(f"[WARN] reports: {e}", file=sys.stderr)

        if nuevos:
            print(f"[ciclo] {len(pairs)} próximos · {nuevos} nuevos · pendientes {len(pending)}",
                  file=sys.stderr)

        # Limpieza de memoria: mantener 'seen' acotado a los últimos ~2000 ids.
        if len(seen) > 3000:
            seen = set(list(seen)[-2000:])

        time.sleep(interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDetenido por el usuario.")
