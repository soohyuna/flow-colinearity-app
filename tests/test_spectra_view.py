"""Checks for the overlap charts. Run with: uv run python tests/test_spectra_view.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

import colinearity as col
import cytek_library as lib
import spectra_view as sv


def test_shared_runs_for_pe_pair():
    rows = {name: lib.signature(name)[1] for name in ("PE", "PE Dazzle 594")}
    sig = pd.DataFrame(rows).T
    sig.columns = sv.channel_names()
    runs = sv.shared_runs(sig.loc["PE"], sig.loc["PE Dazzle 594"])
    assert runs == ["B4–B5", "YG1–YG3"], runs
    caption = sv.pair_caption("PE", "PE Dazzle 594", sig, 0.670844, 0.61846)
    assert "peak YG1, 561 nm" in caption
    assert "Cosine 0.67" in caption
    assert "B4–B5, YG1–YG3" in caption


def test_separate_lasers_do_not_merge():
    channels = sv.channel_names()
    a = pd.Series(0.0, index=channels)
    b = pd.Series(0.0, index=channels)
    a["UV16"] = b["UV16"] = 0.4
    a["V1"] = b["V1"] = 0.4
    assert sv.shared_runs(a, b) == ["UV16", "V1"]


def test_charts_compile():
    rows = {name: lib.signature(name)[1] for name in ("PE", "BV421", "APC/Fire 810")}
    sig = pd.DataFrame(rows).T
    sig.columns = sv.channel_names()
    colors = sv.assign_colors(list(sig.index))
    styles = sv.pair_styles(("PE", "BV421"), list(sig.index), show_rest=True)
    chart = sv.signature_chart(sig, styles, colors, label_dyes=["PE", "BV421"])
    spec = chart.to_dict()
    series = {
        row.get("series")
        for rows in spec["datasets"].values()
        for row in rows
        if isinstance(row, dict) and row.get("series")
    }
    assert "PE · YG1" in series, series
    # The line layer is grouped by laser, so UV16 does not connect to V1.
    line = next(layer for layer in spec["layer"] if layer.get("mark") == {"type": "line"} or layer.get("mark") == "line")
    assert line["encoding"]["detail"]["field"] == "laser"
    heat = sv.heatmap_chart(
        pd.DataFrame(np.eye(3), index=sig.index, columns=sig.index),
        list(sig.index),
        ("PE", "BV421"),
    )
    assert heat.to_dict()["layer"]

    wl = col.WL
    em = {"A": np.exp(-0.5 * ((wl - 520) / 25) ** 2)}
    ex = {"A": np.exp(-0.5 * ((wl - 488) / 40) ** 2)}
    assert sv.emission_chart(em, ex, ["A"], {"A": "#1f4e79"}).to_dict()
    laser = sv.laser_weighted_chart(
        em, ex, {"Blue488": 488.0, "Red640": 640.0}, ["A"], {"A": "#1f4e79"}
    )
    assert laser.to_dict()["facet"]["column"]["field"] == "laser"


if __name__ == "__main__":
    test_shared_runs_for_pe_pair()
    test_separate_lasers_do_not_merge()
    test_charts_compile()
    print("ok")
