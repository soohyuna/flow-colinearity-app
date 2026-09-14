"""Minimal client for pulling dye spectra from the FPbase GraphQL API (fpbase.org).
No 'requests' dependency required -- uses urllib with a browser-like User-Agent,
since FPbase returns 403 to the default urllib UA.
"""
from __future__ import annotations

import json
import re
import time
import difflib
import urllib.request
import urllib.error
from pathlib import Path

GRAPHQL_URL = "https://www.fpbase.org/graphql/"
CACHE_DIR = Path(__file__).parent / "cache"
# v2: dyes *and* fluorescent proteins. The two sets are disjoint on FPbase (974 dye
# names, 509 protein names, zero overlap), and searching dyes only made every reporter
# -- EGFP, tdTomato, mScarlet, mNeonGreen, ZsGreen, DsRed -- unfindable.
DYE_INDEX_CACHE = CACHE_DIR / "fpbase_index_v2.json"
LEGACY_INDEX_CACHE = CACHE_DIR / "fpbase_dye_index.json"
SPECTRA_CATEGORIES = {"D": "dye", "P": "protein"}
HEADERS = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}

# Common flow-cytometry shorthand -> full names as they typically appear on FPbase.
# Applied as candidate expansions during fuzzy matching, not hard overrides.
ALIAS_EXPANSIONS = [
    ("bv", "brilliant violet"),
    ("buv", "bd horizon buv"),
    ("af", "alexa fluor"),
    ("a", "alexa"),  # low-priority, only helps as a secondary candidate
]


def _post_graphql(query: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(
        GRAPHQL_URL, data=json.dumps({"query": query}).encode(), headers=HEADERS
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def get_dye_index(force_refresh: bool = False) -> list[dict]:
    """Every fluorophore spectrum on FPbase: {id, subtype, owner_name, kind}.

    Covers both organic dyes (category D) and fluorescent proteins (category P), so
    reporter lines are searchable alongside antibody conjugates. Cached to disk since
    the list (~3100 entries) changes rarely.
    """
    if not force_refresh and DYE_INDEX_CACHE.exists():
        return json.loads(DYE_INDEX_CACHE.read_text())

    entries = []
    for cat, kind in SPECTRA_CATEGORIES.items():
        body = _post_graphql(f'{{ spectra(category: "{cat}") {{ id subtype owner {{ name }} }} }}')
        entries += [
            {"id": s["id"], "subtype": s["subtype"],
             "owner_name": s["owner"]["name"], "kind": kind}
            for s in body["data"]["spectra"]
        ]
    CACHE_DIR.mkdir(exist_ok=True)
    DYE_INDEX_CACHE.write_text(json.dumps(entries))
    LEGACY_INDEX_CACHE.unlink(missing_ok=True)   # superseded by v2
    return entries


def kind_by_owner(index_entries: list[dict]) -> dict[str, str]:
    """owner_name -> 'dye' | 'protein', for labelling matches in the UI."""
    return {e["owner_name"]: e.get("kind", "dye") for e in index_entries}


def _norm(s: str) -> str:
    return s.lower().replace(" ", "").replace("-", "").replace("/", "").replace(".", "")


_TOKEN_SPLIT = re.compile(r"[ \-/(),]+")
_PARENTHETICAL = re.compile(r"\([^)]*\)")


def _tokens(name: str) -> list[str]:
    return [_norm(t) for t in _TOKEN_SPLIT.split(name) if t.strip()]


def _core(name: str) -> str:
    """Name with any parenthetical gloss removed: FPbase writes the canonical entry for
    a dye as e.g. 'PE (R-PE / R-phycoerythrin)' or 'APC (allophycocyanin)', and the
    part before the parenthesis is the name people actually type.
    """
    return _norm(_PARENTHETICAL.sub("", name))


def _score(nq: str, name: str, penalty: float = 0.0) -> float:
    """Lower is better. Token- and parenthetical-aware, so a short query like 'PE'
    resolves to 'PE (R-PE / R-phycoerythrin)' rather than to 'PE-Cy5' (shorter, also
    starts with the token), 'Alexa Fluor 647-R-phycoerythrin (R-PE)' (substring only),
    or 'PerCP' (different token).
    """
    nn = _norm(name)
    toks = _tokens(name)
    tiebreak = len(nn) / 10000.0
    if nq == nn:
        base = 0.0
    elif nq == _core(name):
        base = 0.5
    elif toks and nq == toks[0]:
        base = 1.0
    elif nq in toks:
        base = 2.0
    elif nn.startswith(nq):
        base = 3.0
    elif nq in nn:
        base = 4.0
    else:
        return float("inf")
    return base + penalty + tiebreak


# Scores at or below this came from a real structural match (exact name, name minus its
# parenthetical gloss, or a whole token) rather than a loose substring or difflib guess.
# 'APC/Fire 810' has no FPbase entry and difflib happily returns 'PE/Fire 780' -- a
# different dye on a different laser -- so anything above this must not be auto-selected.
CONFIDENT_SCORE = 2.6


def suggest_matches(query: str, dye_names: list[str], n: int = 5,
                    with_scores: bool = False):
    """Fuzzy-match a user-supplied fluorophore name against known FPbase dye names.

    With `with_scores`, returns [(name, score), ...] where lower is better and anything
    above CONFIDENT_SCORE is a guess rather than a match (difflib fallbacks score inf).
    """
    nq = _norm(query)
    scored: dict[str, float] = {}

    for name in dye_names:
        s = _score(nq, name)
        if s < float("inf"):
            scored[name] = min(scored.get(name, float("inf")), s)

    # expand common flow shorthand (BV -> Brilliant Violet, BUV -> BD Horizon BUV, ...)
    ql = query.lower().strip()
    for short, long in ALIAS_EXPANSIONS:
        # allow "bv421" (digit right after abbrev), "bv 605" (space), "bv-605" (dash)
        if ql.startswith(short) and (len(ql) == len(short) or not ql[len(short)].isalpha()):
            nexp = _norm(long + ql[len(short):])
            for name in dye_names:
                s = _score(nexp, name, penalty=0.5)
                if s < float("inf"):
                    scored[name] = min(scored.get(name, float("inf")), s)

    ordered = sorted(scored, key=lambda k: scored[k])
    if len(ordered) < n:
        for name in difflib.get_close_matches(query, dye_names, n=n, cutoff=0.5):
            if name not in scored:
                ordered.append(name)
                scored[name] = float("inf")     # a guess, not a match
    ordered = ordered[:n]
    if with_scores:
        return [(name, scored.get(name, float("inf"))) for name in ordered]
    return ordered


def has_confident_match(query: str, dye_names: list[str]) -> bool:
    """True when the best candidate is a real structural match, not a difflib guess."""
    top = suggest_matches(query, dye_names, n=1, with_scores=True)
    return bool(top) and top[0][1] <= CONFIDENT_SCORE


def fetch_spectrum(spectrum_id: str) -> list[list[float]]:
    body = _post_graphql(f"{{ spectrum(id: {spectrum_id}) {{ data }} }}")
    return body["data"]["spectrum"]["data"]


def fetch_dye_curves(owner_name: str, id_by_subtype: dict[str, str]) -> dict:
    """Fetch EM (required) and EX or AB (for excitation weighting) curves for one dye."""
    out = {"fpbase_name": owner_name}
    for subtype in ("EM", "EX", "AB"):
        sid = id_by_subtype.get(subtype)
        if sid:
            out[subtype] = fetch_spectrum(sid)
            time.sleep(0.05)
    return out


def build_owner_lookup(index_entries: list[dict]) -> dict[str, dict[str, str]]:
    """owner_name -> {subtype: spectrum_id}"""
    lookup: dict[str, dict[str, str]] = {}
    for e in index_entries:
        lookup.setdefault(e["owner_name"], {})[e["subtype"]] = e["id"]
    return lookup


CANDIDATE_CACHE = CACHE_DIR / "candidate_spectra.json"


def fetch_candidate_pool(names: list[str], force_refresh: bool = False,
                         progress_cb=None) -> dict:
    """Fetch (and disk-cache) spectra for the replacement-candidate pool.

    Skips names absent from FPbase or lacking both an emission and an
    excitation/absorption curve, since those can't be laser-weighted.
    """
    if not force_refresh and CANDIDATE_CACHE.exists():
        cached = json.loads(CANDIDATE_CACHE.read_text())
        if set(names) <= set(cached) | {n for n in names}:
            missing = [n for n in names if n not in cached]
            if not missing:
                return cached

    lookup = build_owner_lookup(get_dye_index())
    out = json.loads(CANDIDATE_CACHE.read_text()) if CANDIDATE_CACHE.exists() else {}
    for i, name in enumerate(names):
        if name in out:
            continue
        ids = lookup.get(name)
        if not ids or "EM" not in ids or not ({"EX", "AB"} & set(ids)):
            continue
        out[name] = fetch_dye_curves(name, ids)
        if progress_cb:
            progress_cb((i + 1) / len(names), name)
    CACHE_DIR.mkdir(exist_ok=True)
    CANDIDATE_CACHE.write_text(json.dumps(out))
    return out
