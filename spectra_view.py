"""Charts for the panel overlap screen.

The similarity math stays in colinearity.py and recommend.py. This module
turns those results into the signature overlay, the similarity matrix, and
the wavelength charts.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import altair as alt

import colinearity as col
from cytek_channels import CYTEK_5L_CHANNELS

# Fraction of a dye's own peak. Channels at or above this, for both dyes,
# are named in the caption. It is a display rule, not another score.
SHARED_FRACTION = 0.15

# One stable color per dye, in panel order. Repeated only after 18 dyes.
PALETTE = [
    "#1b4f72",  # navy
    "#d35400",  # orange
    "#0e7c66",  # green
    "#6c3483",  # purple
    "#b7950b",  # gold
    "#1a5276",  # blue
    "#922b21",  # brick
    "#c0392b",  # crimson
    "#1e8449",  # leaf
    "#7d3c98",  # violet
    "#b9770e",  # amber
    "#148f77",  # teal
    "#a04000",  # rust
    "#2471a3",  # steel
    "#7b241c",  # maroon
    "#196f3d",  # forest
    "#9a7d0a",  # olive
    "#4a235a",  # plum
]

# (label, laser key, start index, end index, band color)
LASER_BANDS = [
    ("355 nm", "UV355", 0, 16, "#6d4cb0"),
    ("405 nm", "Violet405", 16, 32, "#2f6dc4"),
    ("488 nm", "Blue488", 32, 46, "#249670"),
    ("561 nm", "YG561", 46, 56, "#c4952a"),
    ("640 nm", "Red640", 56, 64, "#b84848"),
]

LASER_NM = {key: label for label, key, _, _, _ in LASER_BANDS}

_CHANNEL_NAMES = [c[0] for c in CYTEK_5L_CHANNELS]
_CHANNEL_META = {c[0]: c for c in CYTEK_5L_CHANNELS}
_INDEX_LASER = {}
for _label, _key, _start, _end, _color in LASER_BANDS:
    for _i in range(_start, _end):
        _INDEX_LASER[_i] = _label


def channel_names() -> list[str]:
    return list(_CHANNEL_NAMES)


def laser_label(channel: str) -> str:
    """'YG1' -> '561 nm'. Unknown channels come back unchanged."""
    meta = _CHANNEL_META.get(channel)
    if meta is None:
        return channel
    return LASER_NM.get(meta[1], meta[1])


def assign_colors(names: list[str], existing: dict[str, str] | None = None) -> dict[str, str]:
    """Keep colors already given out. New names take the next unused swatch."""
    colors = dict(existing or {})
    used = set(colors.values())
    cursor = 0
    for name in names:
        if name in colors:
            continue
        while cursor < 1000:
            swatch = PALETTE[cursor % len(PALETTE)]
            cursor += 1
            if swatch not in used or cursor > len(PALETTE):
                colors[name] = swatch
                used.add(swatch)
                break
    return colors


def order_by_peak(sig: pd.DataFrame) -> list[str]:
    """Dyes in detector order of their peak channel, UV through red."""
    position = {channel: i for i, channel in enumerate(sig.columns)}
    return sorted(sig.index, key=lambda name: position[str(sig.loc[name].idxmax())])


def shared_runs(row_a: pd.Series, row_b: pd.Series, threshold: float = SHARED_FRACTION) -> list[str]:
    """Contiguous channels, within one laser, where both dyes clear the threshold.

    A run of one channel is that channel's name. A longer run is 'B4–B5'.
    """
    channels = list(row_a.index)
    runs: list[str] = []
    i = 0
    n = len(channels)
    while i < n:
        if float(row_a.iloc[i]) >= threshold and float(row_b.iloc[i]) >= threshold:
            laser = _INDEX_LASER.get(i)
            j = i
            while (
                j + 1 < n
                and _INDEX_LASER.get(j + 1) == laser
                and float(row_a.iloc[j + 1]) >= threshold
                and float(row_b.iloc[j + 1]) >= threshold
            ):
                j += 1
            if i == j:
                runs.append(channels[i])
            else:
                runs.append(f"{channels[i]}–{channels[j]}")
            i = j + 1
        else:
            i += 1
    return runs


def format_shared(runs: list[str]) -> str:
    if not runs:
        return (
            "No channel is above 15% of peak for both dyes. "
            "The score comes from a long low tail."
        )
    return "Shared above 15% of peak: " + ", ".join(runs) + "."


def pair_caption(name_a: str, name_b: str, sig: pd.DataFrame, cosine: float, pearson: float) -> str:
    peak_a = str(sig.loc[name_a].idxmax())
    peak_b = str(sig.loc[name_b].idxmax())
    shared = format_shared(shared_runs(sig.loc[name_a], sig.loc[name_b]))
    return (
        f"{name_a} (peak {peak_a}, {laser_label(peak_a)}) and "
        f"{name_b} (peak {peak_b}, {laser_label(peak_b)}). "
        f"Cosine {cosine:.2f}. Pearson {pearson:.2f}. {shared}"
    )


def top_pair(matrix: pd.DataFrame) -> tuple[str, str] | None:
    """Off-diagonal maximum. Ties keep the first pair in matrix order."""
    names = list(matrix.index)
    best: tuple[float, str, str] | None = None
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            value = float(matrix.loc[names[i], names[j]])
            if best is None or value > best[0]:
                best = (value, names[i], names[j])
    if best is None:
        return None
    return best[1], best[2]


def same_pair(pair: tuple[str, str] | None, a: str, b: str) -> bool:
    return pair is not None and {pair[0], pair[1]} == {a, b}


def _color_scale(domain: list[str], colors: dict[str, str]) -> alt.Scale:
    return alt.Scale(domain=domain, range=[colors.get(name, "#607080") for name in domain])


def _channel_axis() -> alt.Axis:
    """Tick every detector, but name only the first channel of each laser.

    Sixty-four rotated names collapse into a smear. Hover still reports the
    channel under the cursor, and the caption names the shared run.
    """
    names = channel_names()
    # Vega expressions cannot call methods, so the lookup is a chain of ternaries.
    # Only the first channel of each laser is named. Hover still reports the rest.
    expr = "''"
    for _label, _key, start, _end, _color in reversed(LASER_BANDS):
        expr = f"datum.value == {start} ? '{names[start]}' : ({expr})"
    return alt.Axis(
        values=list(range(len(names))),
        labelExpr=expr,
        labelAngle=0,
        labelFontSize=11,
        labelOverlap=False,
        labelPadding=4,
        title=None,
        grid=False,
    )


def signature_chart(
    sig: pd.DataFrame,
    styles: dict[str, dict],
    colors: dict[str, str],
    label_dyes: list[str] | None = None,
    ink: str = "#1b2430",
    muted: str = "#5c6b7a",
    grid: str = "#d5dde6",
) -> alt.Chart:
    """Peak-normalized Aurora signatures.

    ``styles`` maps a dye name to opacity, stroke width, and dash
    ('solid', 'dash', or 'dot'). Lines break between lasers. ``label_dyes``
    get a peak-channel label. Pass only the dyes that should appear;
    omitted dyes are not drawn.
    """
    present = [name for name in sig.index if name in styles]
    frame = sig.loc[present]
    channels = list(frame.columns)
    rows: list[dict] = []
    peaks: list[dict] = []
    series_domain: list[str] = []
    series_range: list[str] = []
    for dye in present:
        style = styles[dye]
        values = frame.loc[dye].to_numpy(dtype=float)
        peak_i = int(np.nanargmax(values))
        series = f"{dye} · {channels[peak_i]}"
        series_domain.append(series)
        series_range.append(colors.get(dye, "#607080"))
        for label, _key, start, end, _color in LASER_BANDS:
            for i in range(start, end):
                channel = channels[i]
                meta = _CHANNEL_META[channel]
                rows.append(
                    {
                        "dye": dye,
                        "x": i,
                        "y": float(values[i]),
                        "channel": channel,
                        "center": meta[2],
                        "width_nm": meta[3],
                        "laser": label,
                        "opacity": style.get("opacity", 1),
                        "stroke": style.get("width", 2.2),
                        "dash": style.get("dash", "solid"),
                        "series": series,
                    }
                )
            if end < len(channels):
                rows.append(
                    {
                        "dye": dye,
                        "x": end - 0.5,
                        "y": np.nan,
                        "channel": "",
                        "center": np.nan,
                        "width_nm": np.nan,
                        "laser": label,
                        "opacity": style.get("opacity", 1),
                        "stroke": style.get("width", 2.2),
                        "dash": style.get("dash", "solid"),
                        "series": series,
                    }
                )
        if label_dyes is None or dye in label_dyes:
            peaks.append(
                {
                    "dye": dye,
                    "series": series,
                    "x": peak_i,
                    "y": float(values[peak_i]),
                    "channel": channels[peak_i],
                }
            )

    plot_df = pd.DataFrame(rows)
    band_rows = []
    for label, _key, start, end, color in LASER_BANDS:
        band_rows.append(
            {
                "laser": label,
                "x0": start - 0.5,
                "x1": end - 0.5,
                "y0": 0,
                "y1": 1,
                "mid": (start + end - 1) / 2,
                "short": label.replace(" nm", ""),
                "fill": color,
            }
        )
    bands = pd.DataFrame(band_rows)

    band_layer = (
        alt.Chart(bands)
        .mark_rect(opacity=0.22)
        .encode(
            x=alt.X("x0:Q", scale=alt.Scale(domain=[-0.5, 63.5]), axis=_channel_axis()),
            x2="x1:Q",
            y=alt.Y(
                "y0:Q",
                scale=alt.Scale(domain=[0, 1.12]),
                title="Fraction of peak",
                axis=alt.Axis(grid=True),
            ),
            y2="y1:Q",
            color=alt.Color(
                "laser:N",
                scale=alt.Scale(
                    domain=[row[0] for row in LASER_BANDS],
                    range=[row[4] for row in LASER_BANDS],
                ),
                legend=None,
            ),
        )
    )
    band_labels = (
        alt.Chart(bands)
        .mark_text(dy=-6, fontSize=11, fontWeight=600)
        .encode(
            x=alt.X("mid:Q"),
            y=alt.value(12),
            text="short:N",
            color=alt.value(muted),
        )
    )
    lines = (
        alt.Chart(plot_df)
        .mark_line()
        .encode(
            x="x:Q",
            y="y:Q",
            color=alt.Color(
                "series:N",
                title=None,
                scale=alt.Scale(domain=series_domain, range=series_range),
                legend=alt.Legend(orient="bottom", columns=3, symbolType="stroke", labelLimit=220),
            ),
            strokeDash=alt.StrokeDash(
                "dash:N",
                scale=alt.Scale(
                    domain=["solid", "dash", "dot"],
                    range=[[1, 0], [7, 4], [1.5, 2.5]],
                ),
                legend=None,
            ),
            opacity=alt.Opacity("opacity:Q", legend=None),
            strokeWidth=alt.StrokeWidth("stroke:Q", legend=None),
            detail="laser:N",
            tooltip=[
                alt.Tooltip("dye:N", title="Dye"),
                alt.Tooltip("channel:N", title="Channel"),
                alt.Tooltip("laser:N", title="Laser"),
                alt.Tooltip("center:Q", title="Center (nm)", format=".0f"),
                alt.Tooltip("y:Q", title="Fraction of peak", format=".2f"),
            ],
        )
    )
    layers = [band_layer, band_labels, lines]
    if peaks:
        peak_df = pd.DataFrame(peaks)
        layers.append(
            alt.Chart(peak_df)
            .mark_point(filled=True, size=48)
            .encode(
                x="x:Q",
                y="y:Q",
                color=alt.Color(
                    "series:N",
                    scale=alt.Scale(domain=series_domain, range=series_range),
                    legend=None,
                ),
                tooltip=[alt.Tooltip("dye:N"), alt.Tooltip("channel:N", title="Peak")],
            )
        )

    return (
        alt.layer(*layers)
        .resolve_scale(color="independent", y="shared")
        .properties(height=420, width="container", padding={"top": 8, "bottom": 4})
        .configure_view(strokeWidth=0)
        .configure(background="transparent")
        .configure_axis(labelColor=ink, titleColor=ink, gridColor=grid)
        .configure_legend(labelColor=ink, titleColor=ink)
    )


def pair_styles(pair: tuple[str, str], others: list[str], show_rest: bool) -> dict[str, dict]:
    styles = {
        pair[0]: {"opacity": 1.0, "width": 2.6, "dash": "solid"},
        pair[1]: {"opacity": 1.0, "width": 2.6, "dash": "solid"},
    }
    if show_rest:
        for name in others:
            if name not in styles:
                styles[name] = {"opacity": 0.16, "width": 1.15, "dash": "solid"}
    return styles


def heatmap_chart(
    matrix: pd.DataFrame,
    order: list[str],
    selected: tuple[str, str] | None,
    ink: str = "#1b2430",
    annotate_at: float = 0.4,
    selectable: bool = True,
    scheme: str = "blues",
    domain: tuple[float, float] = (0.0, 1.0),
    diag_color: str = "#e7ebf0",
) -> alt.Chart:
    """Square similarity matrix. Diagonal is muted. The selected cell is outlined."""
    rows = []
    for i, a in enumerate(order):
        for j, b in enumerate(order):
            value = float(matrix.loc[a, b])
            diagonal = a == b
            chosen = selected is not None and {a, b} == set(selected)
            rows.append(
                {
                    "dye_x": a,
                    "dye_y": b,
                    "value": None if diagonal else value,
                    "diag": 1.0 if diagonal else None,
                    "label": f"{value:.2f}" if (not diagonal and (chosen or value >= annotate_at)) else ("1" if diagonal else ""),
                    "label_color": "#8b95a1" if diagonal else ink,
                }
            )
    data = pd.DataFrame(rows)
    x = alt.X(
        "dye_x:N",
        sort=order,
        title=None,
        axis=alt.Axis(labelAngle=-90, labelLimit=160, labelFontSize=10),
    )
    y = alt.Y("dye_y:N", sort=order, title=None)
    cells = (
        alt.Chart(data)
        .mark_rect()
        .encode(
            x=x,
            y=y,
            color=alt.Color(
                "value:Q",
                title=None,
                scale=alt.Scale(domain=list(domain), scheme=scheme),
                legend=alt.Legend(orient="right", format=".1f", title="Similarity"),
            ),
            tooltip=[
                alt.Tooltip("dye_y:N", title="Dye"),
                alt.Tooltip("dye_x:N", title="Dye"),
                alt.Tooltip("value:Q", title="Similarity", format=".2f"),
            ],
        )
    )
    if selectable:
        pick = alt.selection_point(name="cell", fields=["dye_x", "dye_y"], on="click")
        cells = cells.add_params(pick)
    diag = (
        alt.Chart(data)
        .mark_rect(color=diag_color)
        .encode(x=x, y=y)
        .transform_filter("datum.diag != null")
    )
    labels = (
        alt.Chart(data)
        .mark_text(fontSize=9)
        .encode(x=x, y=y, text="label:N", color=alt.Color("label_color:N", scale=None))
        .transform_filter("datum.label != ''")
    )
    layers = [cells, diag, labels]
    if selected is not None and selected[0] in order and selected[1] in order:
        outline_rows = [
            {"dye_x": selected[0], "dye_y": selected[1]},
            {"dye_x": selected[1], "dye_y": selected[0]},
        ]
        layers.append(
            alt.Chart(pd.DataFrame(outline_rows))
            .mark_rect(fill=None, stroke=ink, strokeWidth=2)
            .encode(x=x, y=y)
        )
    height = max(280, 22 * len(order) + 40)
    return (
        alt.layer(*layers)
        .resolve_scale(color="independent")
        .properties(height=height, width="container")
        .configure_view(strokeWidth=0)
        .configure(background="transparent")
        .configure_axis(labelColor=ink, titleColor=ink, grid=False)
        .configure_legend(labelColor=ink, titleColor=ink)
    )


def _curve_frame(series: dict[str, np.ndarray], dyes: list[str], kind: str) -> pd.DataFrame:
    rows = []
    wl = col.WL
    for dye in dyes:
        curve = series.get(dye)
        if curve is None:
            continue
        # Every second nanometre. The shape is unchanged at this scale.
        for i in range(0, len(wl), 2):
            rows.append(
                {
                    "dye": dye,
                    "wavelength": int(wl[i]),
                    "y": float(curve[i]),
                    "kind": kind,
                }
            )
    return pd.DataFrame(rows)


def emission_chart(
    em_curves: dict[str, np.ndarray],
    ex_curves: dict[str, np.ndarray],
    dyes: list[str],
    colors: dict[str, str],
    ink: str = "#1b2430",
    show_lasers: bool = False,
) -> alt.Chart | None:
    """Peak-normalized emission (solid) and excitation (dashed), in nanometres."""
    em = _curve_frame(em_curves, dyes, "emission")
    ex = _curve_frame(ex_curves, dyes, "excitation")
    if em.empty and ex.empty:
        return None
    data = pd.concat([em, ex], ignore_index=True)
    present = [dye for dye in dyes if dye in set(data["dye"])]
    lines = (
        alt.Chart(data)
        .mark_line()
        .encode(
            x=alt.X("wavelength:Q", title="Wavelength (nm)", scale=alt.Scale(domain=[300, 900])),
            y=alt.Y("y:Q", title="Fraction of peak", scale=alt.Scale(domain=[0, 1.05])),
            color=alt.Color("dye:N", title=None, scale=_color_scale(present, colors), legend=alt.Legend(orient="bottom")),
            strokeDash=alt.StrokeDash(
                "kind:N",
                title=None,
                scale=alt.Scale(domain=["emission", "excitation"], range=[[1, 0], [6, 3]]),
            ),
            tooltip=[
                alt.Tooltip("dye:N", title="Dye"),
                alt.Tooltip("kind:N", title="Curve"),
                alt.Tooltip("wavelength:Q", title="nm"),
                alt.Tooltip("y:Q", title="Fraction of peak", format=".2f"),
            ],
        )
    )
    layers = [lines]
    if show_lasers:
        rules = pd.DataFrame({"nm": [355, 405, 488, 561, 640], "laser": ["355", "405", "488", "561", "640"]})
        layers.append(
            alt.Chart(rules)
            .mark_rule(strokeDash=[3, 3], opacity=0.45)
            .encode(x="nm:Q", color=alt.value("#8b95a1"), tooltip=[alt.Tooltip("laser:N", title="Laser (nm)")])
        )
    return (
        alt.layer(*layers)
        .resolve_scale(color="independent", strokeDash="independent")
        .properties(height=280, width="container")
        .configure_view(strokeWidth=0)
        .configure(background="transparent")
        .configure_axis(labelColor=ink, titleColor=ink)
        .configure_legend(labelColor=ink, titleColor=ink)
    )


def laser_weighted_chart(
    em_curves: dict[str, np.ndarray],
    ex_curves: dict[str, np.ndarray],
    lasers: dict[str, float],
    dyes: list[str],
    colors: dict[str, str],
    ink: str = "#1b2430",
) -> alt.Chart | None:
    """One panel per laser: emission multiplied by excitation at that line."""
    rows = []
    wl = col.WL
    for dye in dyes:
        if dye not in em_curves or dye not in ex_curves:
            continue
        em = em_curves[dye]
        ex = ex_curves[dye]
        for lname, nm in lasers.items():
            eff = float(np.interp(nm, wl, ex))
            scaled = em * eff
            for i in range(0, len(wl), 2):
                rows.append(
                    {
                        "dye": dye,
                        "wavelength": int(wl[i]),
                        "y": float(scaled[i]),
                        "laser": lname,
                        "line_nm": nm,
                    }
                )
    if not rows:
        return None
    data = pd.DataFrame(rows)
    present = [dye for dye in dyes if dye in set(data["dye"])]
    order = list(lasers.keys())
    base = alt.Chart(data)
    lines = base.mark_line().encode(
        x=alt.X("wavelength:Q", title="Wavelength (nm)"),
        y=alt.Y("y:Q", title="Emission × excitation"),
        color=alt.Color(
            "dye:N",
            title=None,
            scale=_color_scale(present, colors),
            legend=alt.Legend(orient="bottom"),
        ),
        tooltip=[
            alt.Tooltip("dye:N", title="Dye"),
            alt.Tooltip("laser:N", title="Laser"),
            alt.Tooltip("wavelength:Q", title="nm"),
            alt.Tooltip("y:Q", format=".2f", title="Weighted emission"),
        ],
    )
    # Same frame as the lines so the facet can share one dataset. Rows in a
    # panel share one laser wavelength, so the rules stack on a single x.
    rule = base.mark_rule(strokeDash=[3, 3], opacity=0.45).encode(
        x="line_nm:Q",
        color=alt.value("#8b95a1"),
    )
    return (
        alt.layer(lines, rule)
        .properties(height=170, width=160)
        .facet(column=alt.Column("laser:N", sort=order, title=None))
        .resolve_scale(color="independent")
        .configure_view(strokeWidth=0)
        .configure(background="transparent")
        .configure_axis(labelColor=ink, titleColor=ink)
        .configure_legend(labelColor=ink, titleColor=ink)
        .configure_header(labelColor=ink)
    )


def dyes_missing_curves(names: list[str], curves: dict) -> list[str]:
    return [name for name in names if name not in curves]
