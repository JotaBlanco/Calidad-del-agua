"""
select_provinces.py — Decide which provinces tonight's scrape works on.

The order is fixed and explicit:

  1. Galicia — A Coruña, Lugo, Ourense, Pontevedra.
  2. Every other province, alphabetically by name.

Three rules on top of that order:

  * **A province that did not finish keeps its slot.**  ``progress_{p}.json``
    lists the municipalities already scraped and ``scrape_province()`` skips
    them, so the next night carries on exactly where the 5h budget ran out.
    Unfinished provinces are always picked before new ones.

  * **Free slots go to whichever province has gone longest without a full
    pass**, ties broken by the order above.  ``data/refresh_state.json`` records
    the date each province last *completed* a pass and is committed to the repo,
    so the rotation survives the Actions cache being evicted — which the
    progress files do not.

  * **A finished province is re-scraped with ``--reset``.**  Without it
    ``scrape_province()`` skips every municipality it already has and the data
    would never refresh.

This replaces a day-of-year formula that could only ever reach odd province
codes when a single slot was free — ``(doy * slots + i) % 52 + 1`` is odd for
``i = 0`` — which left Barcelona, Madrid, Valencia and 23 other provinces
permanently unscraped.

Usage:
    python -m scripts.select_provinces --slots 3        # plan for tonight
    python -m scripts.select_provinces --stamp 15 27    # record finished passes
"""

from __future__ import annotations

import argparse
import csv
import collections
import datetime
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "locations_catalog.csv"
PROGRESS_DIR = ROOT / "data" / "progress"
STATE_FILE = ROOT / "data" / "refresh_state.json"

# Province codes that make up Galicia, in the order they are scraped.
GALICIA = [15, 27, 32, 36]


# ── catalog ───────────────────────────────────────────────────────────


def load_catalog() -> tuple[dict[int, int], dict[int, str]]:
    """Return ``({code: n_municipios}, {code: name})`` from the SINAC catalog."""
    with open(CATALOG, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    totals = collections.Counter(int(r["provincia_code"]) for r in rows)
    names = {int(r["provincia_code"]): r["provincia_name"] for r in rows}
    return dict(totals), names


def _sort_key(name: str) -> str:
    """Accent- and case-insensitive sort key, so Álava sorts next to Alava."""
    stripped = unicodedata.normalize("NFD", name)
    return "".join(c for c in stripped if unicodedata.category(c) != "Mn").upper()


def province_order(names: dict[int, str]) -> list[int]:
    """Galicia first, then everything else alphabetically by province name."""
    galicia = [p for p in GALICIA if p in names]
    rest = sorted((p for p in names if p not in galicia),
                  key=lambda p: _sort_key(names[p]))
    return galicia + rest


# ── progress and refresh state ────────────────────────────────────────


def completed(code: int) -> int:
    """How many municipalities of this province are already scraped."""
    path = PROGRESS_DIR / f"progress_{code}.json"
    if not path.exists():
        return 0
    try:
        return len(json.loads(path.read_text(encoding="utf-8"))["completed"])
    except (ValueError, KeyError, OSError):
        return 0


def load_state() -> dict[str, str]:
    """``{province_code: 'YYYY-MM-DD'}`` of the last completed pass."""
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8")).get("ultima_pasada", {})
    except (ValueError, OSError):
        return {}


def save_state(passes: dict[str, str]) -> None:
    STATE_FILE.write_text(
        json.dumps(
            {
                "_comentario": (
                    "Fecha de la última pasada completa por provincia. Lo escribe "
                    "scripts/select_provinces.py --stamp y se commitea cada noche, "
                    "para que la rotación sobreviva al borrado de la caché de Actions."
                ),
                "ultima_pasada": dict(sorted(passes.items(), key=lambda kv: int(kv[0]))),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


# ── selection ─────────────────────────────────────────────────────────


def select(slots: int, override: str = "") -> list[tuple[int, bool, str]]:
    """Pick tonight's provinces.

    Returns ``[(code, reset, description), …]``, at most ``slots`` long.
    """
    totals, names = load_catalog()
    order = province_order(names)
    state = load_state()

    if override.strip():
        picked = [int(x) for x in re.split(r"[,\s]+", override.strip()) if x][:slots]
    else:
        # 1. Provinces mid-pass keep their slot, in the fixed order.
        picked = [p for p in order if 0 < completed(p) < totals.get(p, 0)][:slots]

        # 2. Fill what is left with the least recently refreshed province.
        #    A province that has never completed a pass sorts first ("").
        if len(picked) < slots:
            candidates = sorted(
                (p for p in order if p not in picked),
                key=lambda p: (state.get(str(p), ""), order.index(p)),
            )
            picked += candidates[: slots - len(picked)]

    plan = []
    for code in picked:
        total, done = totals.get(code, 0), completed(code)
        reset = bool(total) and done >= total
        if reset:
            stage = "pasada nueva"
        elif done:
            stage = "reanuda"
        else:
            stage = "empieza"
        last = state.get(str(code))
        seen = f", última pasada {last}" if last else ", nunca completada"
        plan.append((code, reset, f"{code} {names.get(code, code)} ({done}/{total}, {stage}{seen})"))
    return plan


# ── commands ──────────────────────────────────────────────────────────


def cmd_plan(slots: int, override: str) -> None:
    """Emit ``plan``, ``codes`` and ``described`` for the workflow step's outputs.

    ``plan`` drives the scrapers ("15:false 27:true"), ``codes`` is what the
    publish job rebuilds and stamps ("15 27").
    """
    plan = select(slots, override)
    print("plan=" + " ".join(f"{code}:{'true' if reset else 'false'}" for code, reset, _ in plan))
    print("codes=" + " ".join(str(code) for code, _, _ in plan))
    print("described=" + " | ".join(desc for _, _, desc in plan))


def cmd_stamp(codes: list[int]) -> None:
    """Record today's date for every listed province that is now complete."""
    totals, names = load_catalog()
    state = load_state()
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    for code in codes:
        total, done = totals.get(code, 0), completed(code)
        if total and done >= total:
            state[str(code)] = today
            print(f"  {names.get(code, code)}: pasada completa ({done}/{total}) → {today}")
        else:
            print(f"  {names.get(code, code)}: sigue en curso ({done}/{total}), sin marcar")

    save_state(state)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--slots", type=int, help="Plan this many concurrent scrapers")
    parser.add_argument("--override", default=os.environ.get("INPUT_PROVINCIA", ""),
                        help="Scrape these province codes instead of the planned ones")
    parser.add_argument("--stamp", nargs="*", type=int, metavar="CODE",
                        help="Mark these provinces as refreshed if they are complete")
    args = parser.parse_args(argv)

    if args.stamp is not None:
        cmd_stamp(args.stamp)
    elif args.slots:
        cmd_plan(args.slots, args.override)
    else:
        parser.error("pass --slots or --stamp")
    return 0


if __name__ == "__main__":
    sys.exit(main())
