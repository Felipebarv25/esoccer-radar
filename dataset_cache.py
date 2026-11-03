"""Carga rápida del dataset para los modelos (#7, #8).

Leer los ~61k JSON uno por uno tarda minutos. Este módulo los consolida UNA vez
en un solo archivo (data/_dataset.jsonl, una línea por partido con solo lo que
usan los modelos). Las siguientes corridas leen ese único archivo en segundos.

Si aparecen partidos nuevos desde el último consolidado, avisa cuántos faltan;
con rebuild=True (o --rebuild en los scripts) se reconstruye desde cero.
"""
import glob
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_DIR = os.path.join(_HERE, "data", "matches")
_CACHE = os.path.join(_HERE, "data", "_dataset.jsonl")


def _slim(rec: dict) -> dict:
    """Solo los campos que usan los modelos: features + marcador + fecha + jugador1."""
    res = rec.get("result") or None
    if res is not None:
        res = {k: res.get(k) for k in ("outcome", "winner", "sa", "sb")}
    return {
        "match_id": rec.get("match_id"),
        "date": rec.get("date"),
        "player1": rec.get("player1"),
        "features": rec.get("features") or {},
        "result": res,
    }


def _build(files: list) -> list:
    print(f"Consolidando {len(files)} archivos (solo la primera vez)...",
          file=sys.stderr, flush=True)
    recs = []
    with open(_CACHE, "w", encoding="utf-8") as out:
        for i, p in enumerate(files, 1):
            if i % 5000 == 0:
                print(f"  ...{i}/{len(files)}", file=sys.stderr, flush=True)
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    rec = json.load(fh)
            except (json.JSONDecodeError, OSError):
                continue
            s = _slim(rec)
            recs.append(s)
            out.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"Listo: {len(recs)} partidos en cache ({os.path.basename(_CACHE)}).",
          file=sys.stderr, flush=True)
    return recs


def load_records(rebuild: bool = False) -> list:
    """Devuelve la lista de registros (slim). Rápido si ya hay cache."""
    files = glob.glob(os.path.join(_DIR, "*.json"))
    if not rebuild and os.path.exists(_CACHE):
        recs = []
        with open(_CACHE, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        recs.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        nuevos = len(files) - len(recs)
        print(f"Cache: {len(recs)} partidos leídos al instante.", file=sys.stderr, flush=True)
        if nuevos > 500:
            print(f"  (hay ~{nuevos} partidos nuevos desde el último consolidado; "
                  f"corre con --rebuild para incluirlos)", file=sys.stderr, flush=True)
        return recs
    return _build(files)
