"""Cliente de la API oficial de ESportsBattle Football (eSoccer / FIFA 8 min).

Fuente LEGÍTIMA y gratuita: son los datos del propio organizador de los torneos,
servidos como JSON en https://football.esportsbattle.com/api/...
NO scrapeamos sitios de pronósticos (Forebet/Betimate bloquean bots y sus
predicciones son contenido propietario).

Datos que expone:
- nearest_matches(): próximos partidos (jugador1 vs jugador2, fecha, equipos).
- participant(nick): récord de carrera (ganados/empatados/perdidos, totales).
- compare(a, b): stats de carrera de AMBOS lado a lado.
- compare_matches(a, b): historial head-to-head con marcadores (paginado).

NOTA HONESTA: esto son DATOS FACTUALES, no pronósticos ni probabilidades.
"""
import time

import requests

BASE = "https://football.esportsbattle.com/api"
_FINISHED_TOURNAMENT = 3  # status_id de torneo terminado (tiene resultados)


class ESBSource:
    def __init__(self, base: str = BASE, cache_ttl: int = 1800):
        self.base = base
        self.cache_ttl = cache_ttl          # segundos que dura un dato en caché
        self._cache = {}                    # {clave: (timestamp, valor)}
        self.session = requests.Session()
        # Cabeceras de navegador por robustez (por si la API filtra clientes).
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://football.esportsbattle.com/",
        })

    def _get(self, path: str, params: dict = None):
        r = self.session.get(self.base + path, params=params, timeout=20)
        r.raise_for_status()
        time.sleep(0.15)  # cortesía: no martillar la API oficial
        return r.json()

    def _cached(self, key, fn):
        """Devuelve fn() con caché TTL (para datos reusados entre partidos)."""
        now = time.time()
        hit = self._cache.get(key)
        if hit and now - hit[0] < self.cache_ttl:
            return hit[1]
        val = fn()
        self._cache[key] = (now, val)
        return val

    # ---- endpoints ----
    def nearest_matches(self) -> list:
        """Próximos partidos programados (lista)."""
        return self._get("/tournaments/nearest-matches")

    def participant(self, nickname: str) -> dict:
        """Récord de carrera de un jugador."""
        return self._get(f"/participants/{nickname}")

    def compare(self, nick_a: str, nick_b: str) -> list:
        """Stats de carrera de dos jugadores lado a lado (array de 2)."""
        return self._get(f"/participants/{nick_a}/compare/{nick_b}")

    def compare_matches(self, nick_a: str, nick_b: str, page: int = 1) -> dict:
        """Historial head-to-head entre dos jugadores (paginado, ~10/página).

        Devuelve {totalPages, matches}. Cada match trae participant1/2 con
        score (marcador) y el equipo usado; los completados tienen score != null.
        """
        return self._get(f"/participants/{nick_a}/compare/{nick_b}/matches",
                          params={"page": page})

    def participant_tournaments(self, nickname: str, page: int = 1) -> dict:
        return self._get(f"/participants/{nickname}/tournaments", params={"page": page})

    def tournament_results(self, tid) -> dict:
        """Tabla final de un torneo: por jugador W/D/L, GF, GA, GP, posición."""
        return self._cached(f"tres:{tid}", lambda: self._get(f"/tournaments/{tid}/results"))

    def recent_form(self, nickname: str, max_tournaments: int = 6) -> dict:
        """Forma reciente de un jugador agregando sus últimos torneos TERMINADOS.

        Suma GP/W/D/L/GF/GA para sacar goles por partido y % de victoria recientes.
        Cacheado por jugador (se reusa en muchos partidos).
        """
        def compute():
            tour = self.participant_tournaments(nickname, page=1)
            agg = {"games": 0, "win": 0, "draw": 0, "lose": 0, "gf": 0, "ga": 0,
                   "tournaments": 0}
            for t in (tour.get("tournaments") or []):
                if t.get("status_id") != _FINISHED_TOURNAMENT:
                    continue
                res = self.tournament_results(t["id"])
                for row in (res.get("results") or []):
                    if (row.get("participant") or {}).get("nickname") != nickname:
                        continue
                    d = row.get("details") or {}
                    agg["games"] += d.get("GP", 0)
                    agg["win"] += d.get("W", 0)
                    agg["draw"] += d.get("D", 0)
                    agg["lose"] += d.get("L", 0)
                    agg["gf"] += d.get("GF", 0)
                    agg["ga"] += d.get("GA", 0)
                    agg["tournaments"] += 1
                if agg["tournaments"] >= max_tournaments:
                    break
            g = agg["games"]
            agg["gf_per_game"] = round(agg["gf"] / g, 2) if g else None
            agg["ga_per_game"] = round(agg["ga"] / g, 2) if g else None
            agg["win_rate"] = round(agg["win"] / g, 3) if g else None
            return agg
        return self._cached(f"form:{nickname}", compute)


# ---- utilidades de lectura sobre los datos crudos ----
def match_pairs(nearest: list) -> list:
    """Resume nearest_matches() a (fecha, jugador1, jugador2, equipos)."""
    out = []
    for m in nearest or []:
        p1, p2 = m.get("participant1", {}), m.get("participant2", {})
        out.append({
            "match_id": m.get("id"),
            "date": m.get("date"),
            "player1": p1.get("nickname"),
            "team1": (p1.get("team") or {}).get("token_international"),
            "player2": p2.get("nickname"),
            "team2": (p2.get("team") or {}).get("token_international"),
        })
    return out


def career_summary(participant: dict) -> dict:
    """Extrae el récord de carrera con su tasa de victoria."""
    total = participant.get("totalMatches") or 0
    w = participant.get("totalWin") or 0
    d = participant.get("totalDraw") or 0
    l = participant.get("totalLose") or 0
    return {
        "nickname": participant.get("nickname"),
        "matches": total, "win": w, "draw": d, "lose": l,
        "win_rate": round(w / total, 3) if total else None,
    }
