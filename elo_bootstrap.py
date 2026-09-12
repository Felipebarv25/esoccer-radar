"""Bootstrap del Elo con el HISTÓRICO de la API (se corre a mano, 1 vez).

Recorre los torneos TERMINADOS de los últimos N días, baja sus partidos con
marcador, los ordena CRONOLÓGICAMENTE y aplica el Elo partido a partido. Así el
rating arranca reflejando miles de partidos ya jugados, no desde 1500 para todos.

Uso en el servidor:
    source .venv/bin/activate
    python elo_bootstrap.py            # últimos 30 días
    python elo_bootstrap.py 45         # últimos 45 días

Es idempotente (elo.record ignora partidos ya contados), así que se puede repetir
sin doble conteo.
"""
import concurrent.futures
import sys

import requests

import elo
from esb_source import ESBSource, _FINISHED_TOURNAMENT
from datetime import datetime, timedelta, timezone


def run(days: int = 30, max_pages: int = 200, workers: int = 8):
    source = ESBSource()
    now = datetime.now(timezone.utc)
    df = (now - timedelta(days=days)).strftime("%Y/%m/%d %H:%M")
    dt = now.strftime("%Y/%m/%d %H:%M")

    # 1) ids de torneos terminados en la ventana
    tids, page, total = [], 1, 1
    while page <= total and page <= max_pages:
        data = source._get("/tournaments", params={"page": page, "dateFrom": df, "dateTo": dt})
        total = data.get("totalPages", 1)
        for t in data.get("tournaments", []):
            if t.get("status_id") == _FINISHED_TOURNAMENT:
                tids.append(t["id"])
        page += 1
    print(f"[BOOT] {len(tids)} torneos terminados en {days} días")

    # 2) bajar sus partidos en paralelo
    headers = dict(source.session.headers)

    def fetch(tid):
        r = requests.get(f"{source.base}/tournaments/{tid}/matches", headers=headers, timeout=20)
        r.raise_for_status()
        return r.json()

    matches = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in concurrent.futures.as_completed([ex.submit(fetch, t) for t in tids]):
            try:
                data = fut.result()
                arr = data if isinstance(data, list) else (data.get("matches") or [])
                for m in arr:
                    p1, p2 = m.get("participant1", {}), m.get("participant2", {})
                    s1, s2 = p1.get("score"), p2.get("score")
                    n1, n2 = p1.get("nickname"), p2.get("nickname")
                    if None in (s1, s2, n1, n2):
                        continue
                    matches.append((m.get("date", ""), m.get("id"), n1, n2, s1, s2))
            except Exception:
                pass

    # 3) aplicar en orden cronológico
    matches.sort(key=lambda x: x[0])
    applied = 0
    for _date, mid, n1, n2, s1, s2 in matches:
        winner = n1 if s1 > s2 else (n2 if s2 > s1 else None)
        if elo.record(n1, n2, winner, match_id=mid):
            applied += 1
    print(f"[BOOT] {len(matches)} partidos con marcador · {applied} nuevos aplicados")

    print("\n=== TOP 20 por Elo ===")
    for nick, rating, games in elo.top(20):
        print(f"  {rating:7.1f}  {nick}  ({games} part.)")


if __name__ == "__main__":
    dias = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    run(days=dias)
