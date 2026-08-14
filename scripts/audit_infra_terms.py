#!/usr/bin/env python3
"""Audita qué términos de infraestructura hídrica aparecen realmente en los
nombres de punto del cache, y con qué frecuencia.

Sirve para justificar (o descartar) cada entrada de INFRA_EXEMPT_TERMS en
scripts/geo_validate.py, y para detectar términos peligrosos que capturan
puntos que NO son infraestructura (p.ej. "TOMA" captura "TOMA DE MUESTRA").

Uso:
    python scripts/audit_infra_terms.py                 # frecuencias de la lista actual
    python scripts/audit_infra_terms.py --discover      # busca candidatos nuevos
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo_validate import INFRA_EXEMPT_TERMS, _strip_accents  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "processed" / "geocoded_puntos_cache.json"


def punto_names() -> list[str]:
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    return [k.split("_", 2)[2] for k in cache if len(k.split("_", 2)) == 3]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--examples", type=int, default=3)
    args = ap.parse_args()

    names = punto_names()
    upper = [_strip_accents(n).upper() for n in names]
    print(f"nombres de punto en el cache: {len(names):,}\n")

    # --- frecuencia de cada término de la lista actual ---------------------
    print("=" * 78)
    print("FRECUENCIA DE CADA TERMINO DE INFRA_EXEMPT_TERMS")
    print("=" * 78)
    rows = []
    for term in sorted(set(INFRA_EXEMPT_TERMS)):
        rx = re.compile(rf"\b{re.escape(term)}\b")
        hits = [n for n, u in zip(names, upper) if rx.search(u)]
        rows.append((len(hits), term, hits))
    for n, term, hits in sorted(rows, reverse=True):
        flag = "  <-- NO APARECE" if n == 0 else ""
        print(f"  {term:16} {n:6,}{flag}")
        if n and args.examples:
            for h in hits[: args.examples]:
                print(f"       ex: {h[:68]}")

    zero = [t for n, t, _ in rows if n == 0]
    print(f"\nterminos con 0 apariciones ({len(zero)}): {', '.join(zero) or '-'}")

    # --- cobertura total --------------------------------------------------
    big = re.compile(
        r"\b(" + "|".join(sorted(set(INFRA_EXEMPT_TERMS), key=len, reverse=True)) + r")\b"
    )
    matched = sum(1 for u in upper if big.search(u))
    print(f"\npuntos que la lista completa marca como infraestructura: "
          f"{matched:,} / {len(names):,} ({100*matched/len(names):.1f}%)")

    # --- descubrimiento de candidatos -------------------------------------
    if args.discover:
        print("\n" + "=" * 78)
        print("PALABRAS FRECUENTES NO CUBIERTAS (candidatos a revisar a mano)")
        print("=" * 78)
        wc = collections.Counter()
        for u in upper:
            for w in re.findall(r"[A-Z]{4,}", u):
                wc[w] += 1
        known = set(INFRA_EXEMPT_TERMS)
        for w, n in wc.most_common(220):
            if w in known:
                continue
            print(f"  {w:22} {n:6,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
