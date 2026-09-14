"""On-disk store for user-supplied dye data.

Dyes that FPbase does not carry (APC/Fire 810, BUV 615, anything new enough) have to be
supplied by hand -- a CSV, a digitised wavelength spectrum, or a digitised Cytek channel
signature. Redoing that on every app launch is the kind of friction that makes people
quietly drop the dye from the analysis instead, so entries are written to `custom_dyes/`
and reloaded automatically.

One file per dye. The stored payload is exactly the entry dict the rest of the app
passes around ("EM"/"EX"/"AB" curves, or a 64-value "SIGNATURE"), plus provenance so a
number can be traced back to where it came from months later.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

STORE_DIR = Path(__file__).parent / "custom_dyes"
CURVE_KEYS = ("EM", "EX", "AB", "SIGNATURE")


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return s or "dye"


def save(name: str, entry: dict, source: str = "manual", note: str = "") -> Path:
    """Write one dye. `entry` holds any of EM / EX / AB / SIGNATURE."""
    curves = {k: v for k, v in entry.items() if k in CURVE_KEYS}
    if not curves:
        raise ValueError(f"{name}: nothing to save (no EM/EX/AB/SIGNATURE).")
    STORE_DIR.mkdir(exist_ok=True)
    path = STORE_DIR / f"{_slug(name)}.json"
    payload = {
        "name": name,
        "source": source,
        "note": note,
        "saved": datetime.now().isoformat(timespec="seconds"),
        "curves": curves,
    }
    path.write_text(json.dumps(payload))
    return path


def load_all() -> tuple[dict, list]:
    """Returns ({name: entry}, [problem strings]).

    Bad files are reported rather than raised -- one corrupt file should not stop the
    app from starting.
    """
    out, problems = {}, []
    if not STORE_DIR.exists():
        return out, problems
    for path in sorted(STORE_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text())
            name = payload["name"]
            curves = payload["curves"]
            if not any(k in curves for k in CURVE_KEYS):
                raise ValueError("no curve data")
            entry = dict(curves)
            entry["fpbase_name"] = f"(saved: {payload.get('source', 'manual')})"
            out[name] = entry
        except Exception as e:
            problems.append(f"{path.name}: {e}")
    return out, problems


def metadata() -> list[dict]:
    """Provenance for each saved dye, for display."""
    rows = []
    if not STORE_DIR.exists():
        return rows
    for path in sorted(STORE_DIR.glob("*.json")):
        try:
            p = json.loads(path.read_text())
            curves = p.get("curves", {})
            rows.append({
                "dye": p.get("name", path.stem),
                "data": ", ".join(k for k in CURVE_KEYS if k in curves),
                "source": p.get("source", ""),
                "saved": p.get("saved", ""),
                "note": p.get("note", ""),
                "file": path.name,
            })
        except Exception:
            rows.append({"dye": path.stem, "data": "?", "source": "unreadable",
                         "saved": "", "note": "", "file": path.name})
    return rows


def delete(name: str) -> bool:
    path = STORE_DIR / f"{_slug(name)}.json"
    if path.exists():
        path.unlink()
        return True
    return False
