"""Carga rápida y LIVIANA del dataset para los modelos (#7, #8).

Leer los ~61k JSON uno por uno tarda minutos. Este módulo los consolida UNA vez
en un solo archivo (data/_dataset.jsonl, una línea por partido con solo lo que
usan los modelos). Las siguientes corridas leen ese archivo en segundos.

IMPORTANTE (memoria): la VM tiene poca RAM (el bot corre 24/7). Por eso
iter_records() es un GENERADOR que entrega un registro a la vez — nunca mantiene
los 61k en memoria. Los scripts arman arrays numéricos compactos sobre la marcha.
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


def iter_records(rebuild: bool = False):
    """Generador: entrega un registro (slim) a la vez. Bajo consumo de memoria."""
    if not rebuild and os.path.exists(_CACHE):
        print(f"Cache: leyendo {os.path.basename(_CACHE)} en streaming...",
              file=sys.stderr, flush=True)
        with open(_CACHE, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
        return

    files = glob.glob(os.path.join(_DIR, "*.json"))
    print(f"Consolidando {len(files)} archivos (solo la primera vez)...",
          file=sys.stderr, flush=True)
    kept = 0
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
            out.write(json.dumps(s, ensure_ascii=False) + "\n")
            kept += 1
            yield s
    print(f"Listo: {kept} partidos consolidados en {os.path.basename(_CACHE)}.",
          file=sys.stderr, flush=True)
