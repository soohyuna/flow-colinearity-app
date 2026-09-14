"""Panel improvement recommendations.

Scores replacement candidates for one panel slot at a time. Deliberately reports
several metrics rather than a single "best" number, because minimising the single
worst pair can quietly make the panel worse overall -- e.g. on this project's test
panel, swapping Spark Blue 550 -> Alexa Fluor 532 gives the lowest max similarity
(0.60 vs 0.64) but raises the count of pairs above 0.4 from 4 to 6, since AF532 then
conflicts with PE. The BB515 route is the better panel despite the higher headline max.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from cytek_channels import CYTEK_5L_CHANNELS, CYTEK_5L_LASERS
import colinearity as col
import candidates as cand_mod


def _laser_of(peak_channel: str):
    for c, laser, _, _ in CYTEK_5L_CHANNELS:
        if c == peak_channel:
            return laser
    return None


def cytek_signature(dye_entries: dict) -> pd.DataFrame:
    """64-channel Aurora 5L signature per dye.

    Two kinds of entry are accepted:

    * spectra -- {"EM": [[nm, i], ...], "EX"/"AB": ...}, from which the signature is
      computed by weighting the emission curve by excitation efficiency at each laser
      and integrating over each detector's bandpass;
    * a ready-made signature -- {"SIGNATURE": [64 floats]}, read straight off a vendor
      channel plot. This is the instrument's own measured signature, so it is *better*
      than anything derived from reference spectra -- it already includes tandem
      donor bleed-through, filter transmission and detector response. It is also
      Aurora-5L-specific and therefore meaningless in the wavelength-based modes.
    """
    chan_names = [c[0] for c in CYTEK_5L_CHANNELS]

    direct = {n: e["SIGNATURE"] for n, e in dye_entries.items() if "SIGNATURE" in e}
    spectral_entries = {n: e for n, e in dye_entries.items() if "SIGNATURE" not in e}

    rows = {}
    if spectral_entries:
        em, ex = col.build_curves(spectral_entries)
        for n in [k for k in em if k in ex]:
            eff = {l: float(np.interp(w, col.WL, ex[n])) for l, w in CYTEK_5L_LASERS.items()}
            rows[n] = [
                eff[laser] * (em[n][(col.WL >= c - w / 2) & (col.WL <= c + w / 2)].mean()
                              if ((col.WL >= c - w / 2) & (col.WL <= c + w / 2)).any() else 0.0)
                for _, laser, c, w in CYTEK_5L_CHANNELS
            ]
    for n, vals in direct.items():
        v = np.asarray(vals, dtype=float)
        if v.size != len(chan_names):
            raise ValueError(
                f"{n}: signature has {v.size} values, expected {len(chan_names)} "
                "(Aurora 5L: 16 UV + 16 V + 14 B + 10 YG + 8 R)."
            )
        rows[n] = np.clip(v, 0, None)

    # preserve the caller's dye ordering
    ordered = [n for n in dye_entries if n in rows]
    sig = pd.DataFrame([rows[n] for n in ordered], index=ordered, columns=chan_names, dtype=float)
    return sig.div(sig.max(axis=1), axis=0).fillna(0.0)


def _cos(u, v):
    return float(u @ v / (np.linalg.norm(u) * np.linalg.norm(v)))


def cosine_matrix(sig: pd.DataFrame) -> pd.DataFrame:
    """Full pairwise cosine-similarity matrix for a set of channel signatures."""
    names = list(sig.index)
    mat = sig.values
    norm = mat / np.linalg.norm(mat, axis=1, keepdims=True)
    return pd.DataFrame(norm @ norm.T, index=names, columns=names)


def apply_swap(panel_entries: dict, slot: str, candidate: str, candidate_entries: dict) -> dict:
    """Panel with `slot` replaced by `candidate`, preserving the original slot ordering
    so before/after heatmaps line up row-for-row.
    """
    out = {}
    for name, entry in panel_entries.items():
        if name == slot:
            out[candidate] = candidate_entries[candidate]
        else:
            out[name] = entry
    return out


def short_label(name: str, limit: int = 20) -> str:
    """Trim a dye name for heatmap tick labels -- full reagent names such as
    'Fixable Viability Dye eFluor 780' otherwise crowd out the matrix.
    """
    return name if len(name) <= limit else name[: limit - 1].rstrip() + "…"


def panel_metrics(sig: pd.DataFrame) -> dict:
    names = list(sig.index)
    vals = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            vals.append((_cos(sig.loc[names[i]].values, sig.loc[names[j]].values), names[i], names[j]))
    vals.sort(reverse=True)
    if not vals:
        # fewer than two dyes -> no pairs at all; report that rather than IndexError
        return {"max": 0.0, "worst_pair": None,
                "n_over_0.5": 0, "n_over_0.4": 0, "mean": 0.0}
    return {
        "max": vals[0][0],
        "worst_pair": (vals[0][1], vals[0][2]),
        "n_over_0.5": sum(1 for v, _, _ in vals if v > 0.5),
        "n_over_0.4": sum(1 for v, _, _ in vals if v > 0.4),
        "mean": float(np.mean([v for v, _, _ in vals])),
    }


def worst_partner_per_slot(sig: pd.DataFrame) -> pd.DataFrame:
    names = list(sig.index)
    rows = []
    for a in names:
        worst, partner = -1.0, None
        for b in names:
            if a == b:
                continue
            s = _cos(sig.loc[a].values, sig.loc[b].values)
            if s > worst:
                worst, partner = s, b
        rows.append({"dye": a, "worst_similarity": worst, "worst_partner": partner,
                     "peak_channel": sig.loc[a].idxmax()})
    return pd.DataFrame(rows).sort_values("worst_similarity", ascending=False).reset_index(drop=True)


def recommend_for_slot(
    slot: str,
    panel_entries: dict,
    candidate_entries: dict,
    panel_fpnames: dict,
    same_laser_only: bool = True,
    viability_slot: bool = False,
) -> pd.DataFrame:
    """Score every candidate as a replacement for `slot`.

    same_laser_only: restrict to candidates peaking on the same laser as the current
        dye -- keeps the marker's brightness tier and reagent availability plausible.
    viability_slot: treat this slot as the viability dye (no antibody), which lifts the
        same-laser restriction and restricts candidates to viability reagents.
    """
    panel_sig = cytek_signature(panel_entries)
    cand_sig = cytek_signature(candidate_entries)
    if slot not in panel_sig.index:
        return pd.DataFrame()

    slot_laser = _laser_of(panel_sig.loc[slot].idxmax())
    others = [b for b in panel_sig.index if b != slot]
    in_panel = set(panel_fpnames.values())

    base = panel_metrics(panel_sig)
    cur_worst = max(_cos(panel_sig.loc[slot].values, panel_sig.loc[b].values) for b in others)

    rows = []
    for cname in cand_sig.index:
        if cname in in_panel:
            continue
        cand_is_viab = cand_mod.is_viability(cname)
        if viability_slot and not cand_is_viab:
            continue
        if not viability_slot and cand_is_viab:
            continue  # don't propose a viability reagent for an antibody slot
        if same_laser_only and not viability_slot:
            if _laser_of(cand_sig.loc[cname].idxmax()) != slot_laser:
                continue

        v = cand_sig.loc[cname].values
        sims = [_cos(v, panel_sig.loc[b].values) for b in others]
        new_worst = max(sims)

        swapped = {n: panel_entries[n] for n in others}
        swapped[cname] = candidate_entries[cname]
        m = panel_metrics(cytek_signature(swapped))

        rows.append({
            "candidate": cname,
            "peak_channel": cand_sig.loc[cname].idxmax(),
            "laser": _laser_of(cand_sig.loc[cname].idxmax()),
            "slot_worst_after": new_worst,
            "slot_worst_before": cur_worst,
            "slot_improvement": cur_worst - new_worst,
            "new_worst_partner": others[int(np.argmax(sims))],
            "panel_max_after": m["max"],
            "panel_pairs_over_0.5": m["n_over_0.5"],
            "panel_pairs_over_0.4": m["n_over_0.4"],
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df.attrs["baseline"] = base
    return df.sort_values("slot_worst_after").reset_index(drop=True)
