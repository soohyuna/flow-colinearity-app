"""Cytek's own reference library of Aurora 5L channel signatures.

The Full Spectrum Viewer at spectrum.cytekbio.com loads two plain CSVs as part of its
normal page render -- no API, no authentication, just static files. They are cached in
`data/` so the app works offline and does not re-request them.

`data/cytek_5L_signatures.csv` is the valuable one: 342 fluorochromes x 64 detector
channels (UV1..R8) for the 5L 16UV-16V-14B-10YG-8R configuration, as percentages of each
dye's peak channel. This is the instrument vendor's own measured reference signature --
strictly better than anything derived from reference emission/excitation spectra, and
better than digitising a published plot, because it needs no interpolation at all.

Two things to know about the data:

* Values can be slightly negative (a few -1%, -2%). These are real: signatures are
  background-subtracted, so channels with no signal scatter about zero. They are clipped
  to zero on load, which is what the viewer plots.
* Names use Cytek's own conventions -- "BUV615" not "BUV 615", "PE-Dazzle594" not
  "PE Dazzle 594", "APC-Fire 810" not "APC/Fire 810" -- so matching is normalised.

Provenance note: this is Cytek's data, cached for the user's own panel design. Fine for
that; do not redistribute it as your own.
"""
from __future__ import annotations

import csv
import difflib
import urllib.request
from pathlib import Path

import numpy as np

DATA_DIR = Path(__file__).parent / "data"
SIGNATURES_CSV = DATA_DIR / "cytek_5L_signatures.csv"
LASERS_CSV = DATA_DIR / "cytek_fluorochrome_lasers.csv"

# The two static files the Full Spectrum Viewer loads on page render. They are Cytek's
# data, so the repo does not ship them -- each clone fetches them itself on first run,
# which is the same two requests a browser makes when opening the viewer.
SOURCE_URL = "https://spectrum.cytekbio.com/112%20Dyes%20spectrum%205L.csv"
LASERS_URL = "https://spectrum.cytekbio.com/fluorochrome-mapping112.csv"
_UA = {"User-Agent": "Mozilla/5.0"}


def _fetch(url: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
        if b"Fluorochrome" not in body[:200]:      # not the CSV we expected
            return False
        DATA_DIR.mkdir(exist_ok=True)
        dest.write_bytes(body)
        return True
    except Exception:
        return False


def ensure_data(force: bool = False) -> dict:
    """Download the library files if absent. Returns {file: 'present'|'downloaded'|'missing'}."""
    status = {}
    for url, dest in ((SOURCE_URL, SIGNATURES_CSV), (LASERS_URL, LASERS_CSV)):
        if dest.exists() and not force:
            status[dest.name] = "present"
        else:
            status[dest.name] = "downloaded" if _fetch(url, dest) else "missing"
    global _cache
    if any(v == "downloaded" for v in status.values()):
        _cache = None
    return status

CHANNELS = (
    [f"UV{i}" for i in range(1, 17)]
    + [f"V{i}" for i in range(1, 17)]
    + [f"B{i}" for i in range(1, 15)]
    + [f"YG{i}" for i in range(1, 11)]
    + [f"R{i}" for i in range(1, 9)]
)


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


_cache: dict | None = None


def load() -> dict[str, np.ndarray]:
    """{fluorochrome name: 64-value signature, peak-normalised to 1.0}"""
    global _cache
    if _cache is not None:
        return _cache
    if not SIGNATURES_CSV.exists():
        ensure_data()
    if not SIGNATURES_CSV.exists():
        _cache = {}
        return _cache
    out = {}
    with SIGNATURES_CSV.open() as f:
        for row in csv.DictReader(f):
            name = (row.get("Fluorochrome") or "").strip()
            if not name:
                continue
            try:
                vals = np.array([float((row[c] or "0").strip().rstrip("%")) for c in CHANNELS])
            except (KeyError, ValueError):
                continue
            vals = np.clip(vals, 0, None)
            peak = vals.max()
            if peak <= 0:
                continue
            out[name] = vals / peak
    _cache = out
    return out


def laser_notes() -> dict[str, str]:
    """{fluorochrome: excitation laser line(s)} from Cytek's companion mapping file."""
    if not LASERS_CSV.exists():
        return {}
    with LASERS_CSV.open() as f:
        return {
            (r.get("Fluorochrome") or "").strip(): (r.get("Excitation_Laser") or "").strip()
            for r in csv.DictReader(f)
            if (r.get("Fluorochrome") or "").strip()
        }


def find(query: str) -> str | None:
    """Exact-after-normalisation match, then a conservative fuzzy fallback.

    Deliberately stricter than the FPbase matcher: a wrong hit here silently substitutes
    a different dye's measured signature, which is harder to notice than a wrong
    wavelength spectrum.
    """
    lib = load()
    if not lib:
        return None
    nq = _norm(query)
    by_norm = {_norm(k): k for k in lib}
    if nq in by_norm:
        return by_norm[nq]
    close = difflib.get_close_matches(nq, list(by_norm), n=1, cutoff=0.92)
    return by_norm[close[0]] if close else None


def signature(query: str) -> tuple[str, np.ndarray] | None:
    name = find(query)
    return (name, load()[name]) if name else None
