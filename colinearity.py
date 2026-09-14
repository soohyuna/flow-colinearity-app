"""Core colinearity computations, shared by the Streamlit app.

Three modes, in increasing realism:
  1. emission-only   : Pearson r / cosine sim of peak-normalized emission spectra
  2. laser-weighted   : emission spectra weighted by excitation efficiency at each
                        given laser line, concatenated into one signature per dye
  3. cytek64          : laser-weighted signal binned into actual Aurora detector
                        channels (see cytek_channels.py), peak-normalized per dye
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WL = np.arange(300, 901, 1)


def _interp_curve(points: list[list[float]]) -> np.ndarray:
    arr = np.array(points, dtype=float)
    wl, inten = arr[:, 0], np.clip(arr[:, 1], 0, None)
    interp = np.interp(WL, wl, inten, left=0, right=0)
    peak = interp.max()
    return interp / peak if peak > 0 else interp


def build_curves(dye_data: dict[str, dict]) -> tuple[dict, dict]:
    """dye_data: {name: {"EM": [[wl,i],...], "EX"?:..., "AB"?:...}}
    Returns (em_curves, ex_curves) each name -> normalized np.ndarray on WL grid.
    Falls back to AB (absorption) as excitation proxy when EX is absent.
    """
    em_curves, ex_curves = {}, {}
    for name, entry in dye_data.items():
        if "EM" not in entry:
            # e.g. a dye supplied only as a ready-made Cytek channel signature; it has
            # no wavelength curves and simply cannot take part in these modes
            continue
        em_curves[name] = _interp_curve(entry["EM"])
        if "EX" in entry:
            ex_curves[name] = _interp_curve(entry["EX"])
        elif "AB" in entry:
            ex_curves[name] = _interp_curve(entry["AB"])
    return em_curves, ex_curves


def cosine_sim_matrix(mat: np.ndarray) -> np.ndarray:
    norm = mat / np.linalg.norm(mat, axis=1, keepdims=True)
    return norm @ norm.T


def emission_only_similarity(em_curves: dict[str, np.ndarray]):
    names = list(em_curves.keys())
    mat = np.array([em_curves[n] for n in names])
    pearson = pd.DataFrame(np.corrcoef(mat), index=names, columns=names)
    cosine = pd.DataFrame(cosine_sim_matrix(mat), index=names, columns=names)
    return pearson, cosine


def laser_weighted_similarity(em_curves: dict, ex_curves: dict, lasers: dict[str, float]):
    names = [n for n in em_curves if n in ex_curves]
    laser_eff = pd.DataFrame(
        {l: [float(np.interp(w, WL, ex_curves[n])) for n in names] for l, w in lasers.items()},
        index=names,
    )
    blocks = []
    for lname in lasers:
        eff = laser_eff[lname].values.reshape(-1, 1)
        em_mat = np.array([em_curves[n] for n in names])
        blocks.append(eff * em_mat)
    signature = np.concatenate(blocks, axis=1)
    pearson = pd.DataFrame(np.corrcoef(signature), index=names, columns=names)
    cosine = pd.DataFrame(cosine_sim_matrix(signature), index=names, columns=names)
    return pearson, cosine, laser_eff


def cytek64_similarity(em_curves: dict, ex_curves: dict, channels: list[tuple], lasers: dict[str, float]):
    names = [n for n in em_curves if n in ex_curves]
    laser_eff = {n: {l: float(np.interp(w, WL, ex_curves[n])) for l, w in lasers.items()} for n in names}

    chan_names = [c[0] for c in channels]
    sig = pd.DataFrame(index=names, columns=chan_names, dtype=float)
    for n in names:
        em = em_curves[n]
        for chan, laser, center, width in channels:
            lo, hi = center - width / 2, center + width / 2
            mask = (WL >= lo) & (WL <= hi)
            band_em = em[mask].mean() if mask.any() else 0.0
            sig.loc[n, chan] = laser_eff[n][laser] * band_em

    sig_norm = sig.div(sig.max(axis=1), axis=0).fillna(0.0)
    mat = sig_norm.values
    pearson = pd.DataFrame(np.corrcoef(mat), index=names, columns=names)
    cosine = pd.DataFrame(cosine_sim_matrix(mat), index=names, columns=names)
    peak_channel = sig_norm.idxmax(axis=1)
    return pearson, cosine, sig_norm, peak_channel


def ranked_pairs(pearson: pd.DataFrame, cosine: pd.DataFrame | None = None) -> pd.DataFrame:
    """Pairwise similarities, worst first.

    Returns an empty frame *with the right columns* when there are fewer than two dyes.
    Building it from an empty row list instead produced a column-less frame, so the
    sort raised KeyError: 'pearson_r' -- a confusing way to be told the panel is too
    small to have any pairs.
    """
    columns = ["dye_1", "dye_2", "pearson_r"] + (["cosine_sim"] if cosine is not None else [])
    names = list(pearson.index)
    rows = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            row = {"dye_1": a, "dye_2": b, "pearson_r": pearson.loc[a, b]}
            if cosine is not None:
                row["cosine_sim"] = cosine.loc[a, b]
            rows.append(row)
    df = pd.DataFrame(rows, columns=columns)
    if df.empty:
        return df
    return df.sort_values("pearson_r", ascending=False).reset_index(drop=True)
