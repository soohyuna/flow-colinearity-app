"""Digitise a spectrum from a screenshot of a vendor spectra viewer.

For dyes with no numeric data anywhere public (APC/Fire 810 and BUV615 are the two in
this project's reference panel), a screenshot of the vendor's spectra viewer is often
the only source. This extracts (wavelength, intensity) pairs back out of the raster.

Accuracy: digitised curves are approximate -- see `validate.py` for a measured error
estimate against known ground truth. Good enough for panel-design triage, not a
substitute for the vendor's real numbers.

Method: the user supplies the axis calibration (which pixel column is which wavelength,
which pixel row is which intensity) and picks the curve's colour. For each pixel column
inside the plot box the topmost pixel matching that colour is taken as the curve -- this
is deliberate, because spectra viewers usually shade the area under the curve and the
top edge of that shading is the curve itself.
"""
from __future__ import annotations

import numpy as np
from PIL import Image
from scipy import ndimage


def load_rgb(file_or_path) -> np.ndarray:
    """Load an image as an (H, W, 3) uint8 array, flattening any alpha onto white."""
    img = Image.open(file_or_path)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    return np.asarray(img.convert("RGB"))


def dominant_colors(rgb: np.ndarray, box: tuple | None = None, n: int = 12,
                    min_saturation: int = 60, quant: int = 24) -> list[tuple]:
    """Candidate curve colours inside `box`, most frequent first.

    Greys and near-whites are dropped (`min_saturation`), since axes, gridlines, text
    and background are achromatic while plotted curves essentially never are. Colours
    are quantised so antialiasing doesn't split one curve across many near-identical
    entries.

    Returns [((r, g, b), pixel_count), ...] with representative un-quantised colours.
    """
    sub = _crop(rgb, box).reshape(-1, 3).astype(int)
    mx, mn = sub.max(axis=1), sub.min(axis=1)
    keep = (mx - mn) >= min_saturation
    sub = sub[keep]
    if sub.size == 0:
        return []
    q = (sub // quant) * quant
    uniq, counts = np.unique(q, axis=0, return_counts=True)
    order = np.argsort(-counts)[:n]
    out = []
    for i in order:
        key = uniq[i]
        member = sub[np.all(q == key, axis=1)]
        rep = tuple(int(v) for v in np.median(member, axis=0))
        out.append((rep, int(counts[i])))
    return out


def _crop(rgb: np.ndarray, box):
    if box is None:
        return rgb
    x0, y0, x1, y1 = box
    return rgb[int(y0):int(y1), int(x0):int(x1)]


def extract_curve(
    rgb: np.ndarray,
    box: tuple,
    color: tuple,
    x_cal: tuple,
    y_cal: tuple,
    tolerance: int = 40,
    wl_grid: np.ndarray | None = None,
    exclude: list | None = None,
    drop_isolated: bool = False,
    min_component_frac: float = 0.02,
) -> np.ndarray:
    """Pull one curve out of the plot.

    box     : (x0, y0, x1, y1) pixel bounds of the plot area (axes interior)
    color   : (r, g, b) of the curve to follow
    x_cal   : ((px_a, wl_a), (px_b, wl_b)) two x-pixel -> wavelength reference points
    y_cal   : ((py_a, val_a), (py_b, val_b)) two y-pixel -> intensity reference points
    exclude : optional [(x0, y0, x1, y1), ...] image regions to ignore -- use this for
              the legend, whose colour swatch is drawn in the *same* colour as the curve
              and otherwise gets picked up as the topmost pixel, pinning that stretch of
              the curve to a flat wrong value.
    drop_isolated : discard connected blobs smaller than `min_component_frac` of the
              largest one. Off by default: a legend swatch is often *larger* than the
              genuine curve fragments left when another curve crosses over this one, so
              size alone does not separate them and this can remove real data. Use the
              `exclude` box for legends; reach for this only for a clean single-curve
              plot with stray colour-matched text.
    returns : (N, 2) array of [wavelength, intensity], peak-normalised to 1.0
    """
    (pxa, wla), (pxb, wlb) = x_cal
    (pya, va), (pyb, vb) = y_cal
    if pxa == pxb or pya == pyb:
        raise ValueError("Calibration points must differ in pixel position.")

    x0, y0, x1, y1 = (int(v) for v in box)
    sub = rgb[y0:y1, x0:x1].astype(int)
    target = np.array(color, dtype=int)
    dist = np.sqrt(((sub - target) ** 2).sum(axis=2))
    hit = dist <= tolerance

    for ex in exclude or []:
        ex0, ey0, ex1, ey1 = (int(v) for v in ex)
        hit[max(0, ey0 - y0):max(0, ey1 - y0), max(0, ex0 - x0):max(0, ex1 - x0)] = False

    if drop_isolated and hit.any():
        lab, n = ndimage.label(hit)
        if n > 1:
            sizes = ndimage.sum(hit, lab, range(1, n + 1))
            keep = np.flatnonzero(sizes >= sizes.max() * min_component_frac) + 1
            hit = np.isin(lab, keep)

    wls, vals = [], []
    for col in range(hit.shape[1]):
        rows = np.flatnonzero(hit[:, col])
        if rows.size == 0:
            continue
        row = rows.min()                      # topmost = curve (area below may be shaded)
        px, py = x0 + col, y0 + row
        wl = wla + (px - pxa) * (wlb - wla) / (pxb - pxa)
        val = va + (py - pya) * (vb - va) / (pyb - pya)
        wls.append(wl)
        vals.append(val)

    if len(wls) < 5:
        raise ValueError(
            "Found almost no pixels of that colour inside the plot box. Check the colour "
            "selection, raise the tolerance, or re-check the plot box coordinates."
        )

    wls = np.asarray(wls, dtype=float)
    vals = np.clip(np.asarray(vals, dtype=float), 0, None)
    order = np.argsort(wls)
    wls, vals = wls[order], vals[order]
    # one value per wavelength (a steep curve can hit several pixel columns per nm)
    uniq_wl, inv = np.unique(np.round(wls, 3), return_inverse=True)
    uniq_val = np.zeros_like(uniq_wl)
    np.maximum.at(uniq_val, inv, vals)

    if wl_grid is None:
        wl_grid = np.arange(np.floor(uniq_wl.min()), np.ceil(uniq_wl.max()) + 1, 1.0)
    grid_val = np.interp(wl_grid, uniq_wl, uniq_val, left=0.0, right=0.0)
    peak = grid_val.max()
    if peak > 0:
        grid_val = grid_val / peak
    return np.column_stack([wl_grid, grid_val])


def extract_channel_signature(
    rgb: np.ndarray,
    box: tuple,
    color: tuple,
    y_cal: tuple,
    n_channels: int = 64,
    tolerance: int = 50,
    x_first: float | None = None,
    x_last: float | None = None,
    window: int = 6,
) -> np.ndarray:
    """Read a Cytek-style *channel* signature plot (x axis = detector channel, not nm).

    This is a different beast from a wavelength spectrum and much better conditioned:
    the x axis is a fixed number of evenly spaced categories, so instead of tracing a
    continuous curve we sample one value per channel at a known x position. Vendor
    signature plots (BioLegend, Cytek) are exactly this.

    box     : (x0, y0, x1, y1) plot-area pixel bounds
    color   : (r, g, b) of the trace
    y_cal   : ((py_a, val_a), (py_b, val_b)) two y-pixel -> value reference points
    x_first / x_last : pixel x of the first and last channel's marker centre. Defaults
              to the extreme trace pixels inset by the marker radius, which is right for
              a trace that starts at channel 1 and ends at channel `n_channels`.
    window  : half-width in px of the column band sampled around each channel centre.

    returns : (n_channels,) array, peak-normalised to 1.0
    """
    (pya, va), (pyb, vb) = y_cal
    if pya == pyb:
        raise ValueError("Y calibration points must differ in pixel position.")

    x0, y0, x1, y1 = (int(v) for v in box)
    sub = rgb[y0:y1, x0:x1].astype(int)
    dist = np.sqrt(((sub - np.array(color, dtype=int)) ** 2).sum(axis=2))
    hit = dist <= tolerance
    if hit.sum() < n_channels:
        raise ValueError(
            "Too few pixels of that colour inside the plot box -- check the colour, the "
            "tolerance, and the box coordinates."
        )

    ys, xs = np.where(hit)
    if x_first is None or x_last is None:
        # inset by the trace's half-thickness so we land on marker centres, not edges
        thickness = np.median([np.count_nonzero(hit[:, c]) for c in np.unique(xs)])
        inset = max(1.0, thickness / 2.0)
        x_first = xs.min() + inset + x0 if x_first is None else x_first
        x_last = xs.max() - inset + x0 if x_last is None else x_last

    centres = np.linspace(float(x_first), float(x_last), n_channels)
    vals = np.full(n_channels, np.nan)
    for i, cx in enumerate(centres):
        lo = max(0, int(round(cx - window)) - x0)
        hi = min(hit.shape[1], int(round(cx + window)) - x0 + 1)
        band = hit[:, lo:hi]
        rows = np.where(band.any(axis=1))[0]
        if rows.size == 0:
            continue
        # markers are densest at the true value; the median row is robust to the
        # connecting line sweeping through the band on steep segments
        weights = band.sum(axis=1)[rows].astype(float)
        order = np.argsort(rows)
        rows, weights = rows[order], weights[order]
        cum = np.cumsum(weights)
        row = rows[np.searchsorted(cum, cum[-1] / 2.0)]
        py = row + y0
        vals[i] = va + (py - pya) * (vb - va) / (pyb - pya)

    if np.isnan(vals).any():
        missing = np.flatnonzero(np.isnan(vals)) + 1
        raise ValueError(
            f"No trace found for channel(s) {list(missing)} -- widen `window`, raise "
            "`tolerance`, or check x_first / x_last."
        )
    vals = np.clip(vals, 0, None)
    peak = vals.max()
    return vals / peak if peak > 0 else vals


CYTEK_5L_CHANNEL_ORDER = (
    [f"UV{i}" for i in range(1, 17)]
    + [f"V{i}" for i in range(1, 17)]
    + [f"B{i}" for i in range(1, 15)]
    + [f"YG{i}" for i in range(1, 11)]
    + [f"R{i}" for i in range(1, 9)]
)
assert len(CYTEK_5L_CHANNEL_ORDER) == 64


def find_flat_runs(curve: np.ndarray, min_len: int = 5, tol: float = 0.004,
                   min_value: float = 0.15, long_run: int = 11,
                   step_down: float = 0.02) -> list[tuple]:
    """Stretches where the digitised curve is suspiciously constant.

    A legend swatch drawn in the curve's own colour sits above the curve and pins that
    span to one flat value -- the single most common way an otherwise-good digitisation
    goes wrong. Real emission spectra are essentially never dead flat away from zero.

    Two signatures are reported, because length alone does not separate the cases:

    * a **long** flat run (>= `long_run` samples) -- a solid legend line;
    * a **shorter** flat run that is a local-maximum plateau, i.e. the curve steps down
      by at least `step_down` on *both* sides -- a dashed legend line, which breaks into
      several short runs. A dashed *curve* interpolating across its own gaps produces
      flat runs that lie between their neighbours rather than above them, so this test
      passes them over.

    Returns [(start_wl, end_wl, value), ...].
    """
    wl, v = curve[:, 0], curve[:, 1]
    runs, i, n = [], 0, len(v)
    while i < n:
        j = i + 1
        while j < n and abs(v[j] - v[i]) <= tol:
            j += 1
        length = j - i
        if length >= min_len and v[i] >= min_value:
            if length >= long_run:
                runs.append((float(wl[i]), float(wl[j - 1]), float(v[i])))
            else:
                left = v[i - 1] if i > 0 else v[i]
                right = v[j] if j < n else v[j - 1]
                if (v[i] - left) >= step_down and (v[i] - right) >= step_down:
                    runs.append((float(wl[i]), float(wl[j - 1]), float(v[i])))
        i = j
    return runs
