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
from datetime import datetime, timedelta, timezone

import requests

BASE = "https://football.esportsbattle.com/api"
_FINISHED_TOURNAMENT = 3  # status_id de torneo terminado (tiene resultados)
_SCHEDULED_MATCH = 1      # status_id de partido programado (aún no empieza)


def match_type(token: str) -> str:
    """Tipo de partido según el nombre del torneo/liga. Sin número = 2x4 (8 min)."""
    t = token or ""
    if "2x6" in t:
        return "2x6"
    if "2x5" in t:
        return "2x5"
    return "2x4"


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
        """Próximos partidos programados (lista) — SOLO 2x4. Legacy."""
        return self._get("/tournaments/nearest-matches")

    def upcoming_matches(self, lookahead_min: int = 25,
                         back_hours: int = 1, fwd_hours: int = 3) -> list:
        """Próximos partidos de TODOS los tipos (2x4/2x5/2x6), con etiqueta de tipo.

        Recorre los torneos activos/próximos y saca sus partidos programados que
        empiezan dentro de los próximos `lookahead_min` minutos. Cada partido lleva
        `_type` (2x4/2x5/2x6). Los partidos de cada torneo se cachean.
        """
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(minutes=lookahead_min)
        date_from = (now - timedelta(hours=back_hours)).strftime("%Y/%m/%d %H:%M")
        date_to = (now + timedelta(hours=fwd_hours)).strftime("%Y/%m/%d %H:%M")

        out, page, total_pages = [], 1, 1
        while page <= total_pages and page <= 5:
            data = self._get("/tournaments", params={
                "page": page, "dateFrom": date_from, "dateTo": date_to})
            total_pages = data.get("totalPages", 1)
            for t in data.get("tournaments", []):
                if t.get("status_id") == _FINISHED_TOURNAMENT:
                    continue
                ttype = match_type(t.get("token_international"))
                tid = t["id"]
                tm = self._cached(
                    f"tmatches:{tid}",
                    lambda i=tid: self._get(f"/tournaments/{i}/matches"))
                matches = tm if isinstance(tm, list) else (tm.get("matches") or [])
                for m in matches:
                    if m.get("status_id") != _SCHEDULED_MATCH:
                        continue
                    try:
                        md = datetime.fromisoformat((m.get("date") or "").replace("Z", "+00:00"))
                    except Exception:
                        continue
                    if now <= md <= cutoff:
                        m["_type"] = ttype
                        out.append(m)
            page += 1
        # ordenar por fecha
        out.sort(key=lambda m: m.get("date") or "")
        return out

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

    def match_result(self, nick_a: str, nick_b: str, match_id) -> dict:
        """Busca el resultado REAL de un partido ya jugado (marcador + ganador).

        Devuelve None si aún no tiene marcador (no ha terminado o hay lag).
        {a, sa, b, sb, winner} donde winner es el nickname ganador o None (empate).
        """
        for page in (1, 2):
            data = self.compare_matches(nick_a, nick_b, page=page)
            for m in data.get("matches", []):
                if str(m.get("id")) != str(match_id):
                    continue
                p1, p2 = m.get("participant1", {}), m.get("participant2", {})
                s1, s2 = p1.get("score"), p2.get("score")
                if s1 is None or s2 is None:
                    return None  # sin marcador todavía
                # normalizar a A/B por nickname
                if p1.get("nickname") == nick_a:
                    sa, sb = s1, s2
                else:
                    sa, sb = s2, s1
                if sa > sb:
                    winner = nick_a
                elif sb > sa:
                    winner = nick_b
                else:
                    winner = None
                return {"a": nick_a, "sa": sa, "b": nick_b, "sb": sb, "winner": winner}
        return None

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
            "match_type": m.get("_type"),
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
