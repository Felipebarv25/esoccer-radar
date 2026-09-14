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

import analytics
import backtest
import config
import persistence
import reports
import team_ratings
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
    notifier = TelegramNotifier()                                  # canal de análisis
    reports_notifier = TelegramNotifier(chat_id=config.TELEGRAM_REPORTS_CHAT_ID)  # canal de reportes

    # Escucha de comandos a pedido (/semana, /mes, ...) por chat privado con el bot.
    try:
        import commands
        commands.start_in_thread()
    except Exception as e:
        print(f"[WARN] no pude iniciar comandos: {e}", file=sys.stderr)

    # Vigilancia de salud: avisa al canal de reportes si la API se bloquea o
    # deja de emitir partidos por mucho tiempo.
    from watchdog import Watchdog
    health = Watchdog(reports_notifier)
    seen = persistence.already_saved_ids()   # no re-avisar los ya guardados
    pending = persistence.load_open()        # partidos alertados sin desenlace aún

    interval = config.POLL_INTERVAL_SECONDS
    print(f"eSoccer Radar — intervalo {interval}s · ya guardados: {len(seen)}")
    if not args.no_startup:
        notifier.send("🟢 <b>eSoccer Radar activo</b>\nAnalizando próximos partidos de "
                      "eSports Battle. <i>Datos, NO pronósticos.</i>")
    # Reporte de arranque al canal de reportes: confirma que quedó conectado
    # sin esperar a la hora programada. Va aunque --no-startup silencie el canal
    # de análisis, porque el de reportes es silencioso (solo en reinicios).
    try:
        reports.send_startup(reports_notifier)
    except Exception as e:
        print(f"[WARN] reports.send_startup: {e}", file=sys.stderr)

    while True:
        try:
            pairs = match_pairs(source.upcoming_matches())
        except Exception as e:
            print(f"[WARN] fallo al listar próximos: {e}", file=sys.stderr)
            health.fetch_error()
            time.sleep(interval)
            continue
        health.ok_fetch(len(pairs))

        nuevos = 0
        edge_views = None   # stats acumuladas, se cargan una vez por ciclo (perezoso)
        hour_tbl = None     # temperatura por franja horaria (se calcula una vez)
        top_combos = None   # {(jugador, equipo): fila} del TOP 50 (oportunidades)
        for p in pairs:
            mid = str(p.get("match_id"))
            if mid in seen or not p.get("player1") or not p.get("player2"):
                continue
            seen.add(mid)
            try:
                a = analyze_match(source, p["player1"], p["player2"],
                                  team_a=p.get("team1"), team_b=p.get("team2"))
            except Exception as e:
                print(f"[WARN] no pude analizar {p['player1']} vs {p['player2']}: {e}",
                      file=sys.stderr)
                continue
            # Ventajas históricas ("on fire") + temperatura de la hora.
            edges = []
            hour_stat = None
            try:
                if edge_views is None:
                    edge_views = analytics.load_views()
                    hour_tbl = analytics.hour_table(edge_views)
                    top_combos = {(r["player"], r["team"]): r
                                  for r in team_ratings.top(min_games=10, limit=50)}
                hora = None
                sc = analytics._parse(p.get("date"))
                if sc:
                    hora = sc.astimezone(analytics._COL).hour
                edges = analytics.detect_edges(
                    edge_views, p["player1"], p.get("team1"),
                    p["player2"], p.get("team2"), hora)
                if hora is not None and hour_tbl:
                    hs = hour_tbl.get(hora)
                    if hs:
                        hour_stat = {"hora": hora, **hs}
            except Exception as e:
                print(f"[WARN] detect_edges {mid}: {e}", file=sys.stderr)

            # ¿El favorito juega con un equipo del TOP 50? → oportunidad destacada.
            top_hit = None
            fav = a.get("favored")
            if fav and top_combos:
                fteam = (p.get("team1") if fav == p["player1"]
                         else (p.get("team2") if fav == p["player2"] else None))
                if fteam:
                    top_hit = top_combos.get((fav, fteam))
            # Foto pre-partido de Elo (para mostrar junto al score y comparar).
            elo_snap = None
            try:
                import elo
                elo_snap = elo.snapshot(p["player1"], p["player2"])
            except Exception as e:
                print(f"[WARN] elo.snapshot {mid}: {e}", file=sys.stderr)
            msg_id = notifier.send(format_match(p, a, edges=edges, elo=elo_snap,
                                                hour_stat=hour_stat, top_combo=top_hit))   # enviar primero → obtener message_id
            rec = persistence.save_analysis(p, a, message_id=msg_id, elo=elo_snap)
            pending.append(rec)
            nuevos += 1
            print(f"[MATCH] {p['player1']} vs {p['player2']} → score {a['score_a']} "
                  f"| Telegram {'OK' if msg_id else 'FALLÓ'}", file=sys.stderr)

        # Cerrar los partidos que ya terminaron (backtesting: ✅/❌ bajo la alerta).
        try:
            pending = backtest.process(source, notifier, pending)
        except Exception as e:
            print(f"[WARN] backtest.process: {e}", file=sys.stderr)

        # Reportes programados (van al canal de reportes, no al de análisis).
        try:
            reports.maybe_hourly(reports_notifier)       # resumen de cada hora
            reports.maybe_send(reports_notifier)          # tasa + análisis profundo diario
            reports.maybe_team_report(reports_notifier)   # CSV jugador-equipo cada 2 días
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
