"""
Fluorophore Colinearity Explorer
---------------------------------
Streamlit GUI for spectral colinearity analysis of a flow panel:
  1. paste a fluorophore list; each name resolves against Cytek's official Aurora 5L
     signature library *and* FPbase (the two complement each other -- Cytek supplies the
     measured 64-channel signature, FPbase the wavelength curves)
  2. (optionally) upload or digitise data for dyes neither source has, or to override
  3. compute pairwise similarity: emission-only, laser-weighted, or binned into
     a real spectral cytometer's detector channels (e.g. Cytek Aurora 5L)
  4. view heatmaps + ranked pairs table, download PNG/CSV

Run with:  streamlit run app.py
"""
import io
import json

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import streamlit as st

import fpbase_client as fp
import colinearity as col
import recommend as rec
import candidates as cand_mod
import spectra_image as si
import custom_store
import cytek_library
import spectra_sources
from cytek_channels import CYTEK_5L_CHANNELS, LASER_PRESETS

st.set_page_config(page_title="Fluorophore Colinearity Explorer", layout="wide")
st.title("Fluorophore Colinearity Explorer")
st.caption(
    "Pairwise spectral similarity ('colinearity') for a flow panel. Signatures come from "
    "**Cytek's official Aurora 5L library** where available, with **FPbase** wavelength "
    "spectra alongside for the emission-only and generic-laser modes; anything neither "
    "has can be digitised from a vendor plot."
)

# Reference panel. APC/Fire 810 and BUV 615 have no FPbase entry as of Aug 2026 -- they
# are kept in the list on purpose so the "not found" path (CSV or screenshot) is visible
# rather than the panel silently shrinking.
EXAMPLE_LIST = """Zombie NIR
BUV395
PE
PE-Cy7
Alexa Fluor 700
BB700
Spark Blue 550
PE Dazzle 594
APC/Fire 810
BV510
BUV 737
BUV 805
BV421
Alexa Fluor 647
Super Bright 780
BV 605
BUV 615
Alexa Fluor 488"""

if "selections" not in st.session_state:
    st.session_state.selections = {}
if "custom_curves" not in st.session_state:
    # dyes digitised or uploaded in an earlier session come back automatically
    saved, load_problems = custom_store.load_all()
    st.session_state.custom_curves = saved
    st.session_state.custom_load_problems = load_problems
if "dye_data" not in st.session_state:
    st.session_state.dye_data = {}


def remember_custom(name: str, entry: dict, source: str, note: str = ""):
    """Hold a custom dye in session and persist it to custom_dyes/."""
    st.session_state.custom_curves[name] = entry
    try:
        custom_store.save(name, entry, source=source, note=note)
        return True, None
    except Exception as e:
        return False, str(e)

def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(c):
    return "#%02x%02x%02x" % tuple(int(v) for v in c)


def _pixel_ruler(rgb, caption_extra=""):
    H, W = rgb.shape[:2]
    fig, ax = plt.subplots(figsize=(9, 9 * H / W))
    ax.imshow(rgb)
    ax.set_xlabel("x pixel"); ax.set_ylabel("y pixel")
    ax.grid(color="cyan", alpha=0.35, lw=0.5)
    ax.set_xticks(np.linspace(0, W, 11).astype(int))
    ax.set_yticks(np.linspace(0, H, 11).astype(int))
    ax.tick_params(labelsize=7)
    plt.tight_layout()
    st.pyplot(fig)
    st.caption(f"Image is {W}×{H} px. {caption_extra}")
    return H, W


def manual_source_ui(q: str, key_prefix: str = "manual"):
    """Supply data for a dye regardless of whether it already matched a source."""
    ns = "_" + key_prefix
    existing = st.session_state.custom_curves.get(q)
    if existing:
        have = ", ".join(k for k in ("SIGNATURE", "EM", "EX", "AB") if k in existing)
        st.info(f"**{q}** already has custom data ({have}). Adding again replaces it.")
    src = st.radio(
        "Source",
        ["Screenshot: Cytek channel signature",
         "Screenshot: wavelength spectrum",
         "CSV file"],
        key=f"msrc_{q}{ns}",
        help="Screenshot of a plot whose x axis reads 'Emission Channel' (UV1…R8) → "
             "first option. Plot in nanometres → second. CSV only for numeric data.",
    )
    if src == "Screenshot: Cytek channel signature":
        signature_digitizer_ui(q, ns=ns)
    elif src == "Screenshot: wavelength spectrum":
        image_digitizer_ui(q, ns=ns)
    else:
        st.caption("Numeric CSV: wavelength, emission, [excitation].")
        up = st.file_uploader(f"mupload_{q}", key=f"mupload_{q}{ns}",
                              label_visibility="collapsed", type=["csv", "txt", "tsv"])
        if up is not None:
            head = up.read(8); up.seek(0)
            if head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8")):
                st.error("That's an image — pick one of the screenshot options above.")
            else:
                try:
                    df = pd.read_csv(up)
                    df.columns = [c.strip().lower() for c in df.columns]
                    miss = {"wavelength", "emission"} - set(df.columns)
                    if miss:
                        raise ValueError("missing column(s): " + ", ".join(sorted(miss)))
                    entry = {"EM": df[["wavelength", "emission"]].values.tolist()}
                    if "excitation" in df.columns:
                        entry["EX"] = df[["wavelength", "excitation"]].values.tolist()
                    ok, err = remember_custom(q, entry, source="CSV upload",
                                              note=f"{len(df)} points")
                    st.success(f"Loaded {len(df)} points for {q}."
                               + ("  \nStored in `custom_dyes/`." if ok else ""))
                    if not ok:
                        st.warning(f"Session only — disk write failed: {err}")
                except Exception as e:
                    st.error(f"Could not read that CSV — {e}")


def signature_digitizer_ui(q: str, ns: str = ""):
    """Read a Cytek-style 64-channel signature plot (x axis = detector channel).

    Preferred over the wavelength digitiser whenever the vendor publishes one: it is the
    instrument's own measured signature, so it already contains tandem donor bleed-through,
    filter transmission and detector response that reference spectra do not capture.
    """
    st.caption(
        "For plots whose x axis is **Emission Channel** (UV1…UV16, V1…V16, B1…B14, "
        "YG1…YG10, R1…R8) — e.g. BioLegend's or Cytek's signature plots. This is the "
        "Aurora's own measured signature and is **more accurate than reference spectra**, "
        "because it already includes tandem bleed-through and detector response. It is "
        "specific to the 5L Aurora, so a dye supplied this way only works in the "
        "**Cytek 5L 64-channel** mode."
    )
    up = st.file_uploader("sig image", type=["png", "jpg", "jpeg"], key=f"sig_{q}{ns}",
                          label_visibility="collapsed")
    if up is None:
        return
    try:
        rgb = si.load_rgb(up)
    except Exception as e:
        st.error(f"Could not read image: {e}")
        return
    H, W = _pixel_ruler(rgb, "Read the plot-area corners and two y-axis gridlines off the grid.")

    c1, c2, c3, c4 = st.columns(4)
    bx0 = c1.number_input("box left x", 0, W, 0, key=f"sbx0_{q}{ns}")
    by0 = c2.number_input("box top y", 0, H, 0, key=f"sby0_{q}{ns}")
    bx1 = c3.number_input("box right x", 0, W, W, key=f"sbx1_{q}{ns}")
    by1 = c4.number_input("box bottom y", 0, H, H, key=f"sby1_{q}{ns}")
    if bx1 <= bx0 or by1 <= by0:
        st.error("Plot box is empty.")
        return

    d1, d2, d3, d4 = st.columns(4)
    py_lo = d1.number_input("y pixel of a known value", 0, H, 0, key=f"sy0_{q}{ns}")
    v_lo = d2.number_input("…its value", -10.0, 10.0, 0.0, key=f"sv0_{q}{ns}")
    py_hi = d3.number_input("y pixel of another value", 0, H, 0, key=f"sy1_{q}{ns}")
    v_hi = d4.number_input("…its value", -10.0, 10.0, 1.0, key=f"sv1_{q}{ns}")
    if py_lo == py_hi:
        st.error("The two y reference pixels must differ.")
        return

    found = si.dominant_colors(rgb, (bx0, by0, bx1, by1), n=8)
    if found:
        st.markdown(" ".join(
            f"<span style='display:inline-block;width:52px;height:18px;background:{_rgb_to_hex(c)};"
            f"border:1px solid #999;margin-right:4px'></span>" for c, _ in found
        ), unsafe_allow_html=True)
        st.caption(" ".join(f"`{_rgb_to_hex(c)}`" for c, _ in found))
    col_hex = st.color_picker("Trace colour", _rgb_to_hex(found[0][0]) if found else "#9d82e1",
                              key=f"scol_{q}{ns}")
    tol = st.slider("Colour tolerance", 10, 120, 50, key=f"stol_{q}{ns}")

    if not st.button("Extract 64-channel signature", key=f"sgo_{q}{ns}"):
        return
    try:
        sig64 = si.extract_channel_signature(
            rgb, (bx0, by0, bx1, by1), _hex_to_rgb(col_hex),
            y_cal=((py_lo, v_lo), (py_hi, v_hi)), tolerance=tol,
        )
    except ValueError as e:
        st.error(str(e))
        return

    names = si.CYTEK_5L_CHANNEL_ORDER
    fig, ax = plt.subplots(figsize=(11, 3))
    ax.plot(range(64), sig64, color=col_hex, marker="o", ms=3, lw=1.4)
    ax.set_xticks(range(64)); ax.set_xticklabels(names, rotation=90, fontsize=5.5)
    ax.set_ylabel("Normalized"); ax.grid(alpha=0.3)
    ax.set_title(f"{q} — digitised Aurora 5L signature (peak {names[int(np.argmax(sig64))]})")
    plt.tight_layout()
    st.pyplot(fig)

    peak = names[int(np.argmax(sig64))]
    ok, err = remember_custom(q, {"SIGNATURE": sig64.tolist()},
                              source="digitised Cytek channel signature",
                              note=f"peak {peak}")
    st.success(
        f"Saved 64-channel signature for {q}. Peak channel **{peak}**. "
        "Use the *Cytek Aurora 5L* mode in Tab 2."
        + ("  \nStored in `custom_dyes/` — it will load automatically next time."
           if ok else "")
    )
    if not ok:
        st.warning(f"Held for this session only — could not write to disk: {err}")
    st.download_button(
        "Download signature CSV",
        pd.Series(sig64, index=names, name="normalized_signal").to_csv(),
        f"{q.replace('/', '_').replace(' ', '_')}_signature.csv", "text/csv",
        key=f"sdl_{q}{ns}",
    )


def image_digitizer_ui(q: str, ns: str = ""):
    """Extract a spectrum from a screenshot of a vendor spectra viewer.

    Needed for dyes with no numeric data anywhere public -- APC/Fire 810 and BUV615
    are the two in the reference panel.
    """
    st.caption(
        "For dyes with no published numeric data. Works best on a plot showing **only "
        "this dye**, cropped so the axes are visible. Digitised curves are approximate "
        "(validated at ~0.006 mean absolute error on a known spectrum), which is fine "
        "for panel triage but is not vendor data."
    )
    up = st.file_uploader("image", type=["png", "jpg", "jpeg"], key=f"img_{q}{ns}",
                          label_visibility="collapsed")
    if up is None:
        return

    try:
        rgb = si.load_rgb(up)
    except Exception as e:
        st.error(f"Could not read image: {e}")
        return
    H, W = rgb.shape[:2]

    fig, ax = plt.subplots(figsize=(9, 9 * H / W))
    ax.imshow(rgb)
    ax.set_xlabel("x pixel"); ax.set_ylabel("y pixel")
    ax.grid(color="cyan", alpha=0.35, lw=0.5)
    ax.set_xticks(np.linspace(0, W, 11).astype(int))
    ax.set_yticks(np.linspace(0, H, 11).astype(int))
    ax.tick_params(labelsize=7)
    plt.tight_layout()
    st.pyplot(fig)
    st.caption(
        f"Image is {W}×{H} px. Read the plot-area corners off the grid above: the "
        "**plot box** is the axes interior — where the data actually lives, not "
        "including tick labels."
    )

    c1, c2, c3, c4 = st.columns(4)
    bx0 = c1.number_input("box left x", 0, W, 0, key=f"bx0_{q}{ns}")
    by0 = c2.number_input("box top y", 0, H, 0, key=f"by0_{q}{ns}")
    bx1 = c3.number_input("box right x", 0, W, W, key=f"bx1_{q}{ns}")
    by1 = c4.number_input("box bottom y", 0, H, H, key=f"by1_{q}{ns}")
    if bx1 <= bx0 or by1 <= by0:
        st.error("Plot box is empty — right x must exceed left x, and bottom y exceed top y.")
        return
    box = (bx0, by0, bx1, by1)

    a1, a2, a3, a4 = st.columns(4)
    wl_lo = a1.number_input("wavelength at left edge (nm)", 200, 1200, 350, key=f"wl0_{q}{ns}")
    wl_hi = a2.number_input("wavelength at right edge (nm)", 200, 1200, 900, key=f"wl1_{q}{ns}")
    v_bot = a3.number_input("value at bottom edge", 0.0, 1000.0, 0.0, key=f"v0_{q}{ns}")
    v_top = a4.number_input("value at top edge", 0.0, 1000.0, 100.0, key=f"v1_{q}{ns}")
    if wl_hi <= wl_lo:
        st.error("Right-edge wavelength must be greater than left-edge wavelength.")
        return

    found = si.dominant_colors(rgb, box, n=8)
    if found:
        st.caption("Strongest non-grey colours inside the plot box (likely curves):")
        st.markdown(" ".join(
            f"<span style='display:inline-block;width:52px;height:18px;background:{_rgb_to_hex(c)};"
            f"border:1px solid #999;margin-right:4px' title='{_rgb_to_hex(c)} — {n}px'></span>"
            for c, n in found
        ), unsafe_allow_html=True)
        st.caption(" ".join(f"`{_rgb_to_hex(c)}`" for c, _ in found))

    which = st.radio("Curve to extract", ["Emission", "Excitation"],
                     key=f"which_{q}{ns}", horizontal=True)
    default_hex = _rgb_to_hex(found[0][0]) if found else "#d62728"
    col_hex = st.color_picker("Curve colour", default_hex, key=f"col_{q}_{which}{ns}")
    tol = st.slider("Colour tolerance", 10, 120, 60, key=f"tol_{q}_{which}{ns}",
                    help="Raise if the curve is missed in places; lower if a "
                         "different curve of a similar colour is being picked up.")

    use_excl = st.checkbox(
        "Exclude a region (use for the legend)", key=f"useexcl_{q}_{which}{ns}",
        help="A legend's colour swatch is drawn in the same colour as the curve and sits "
             "above it, pinning that span to a flat wrong value. Draw the box tightly "
             "around the swatch only — a loose box will also delete real curve data.",
    )
    excl = None
    if use_excl:
        e1, e2, e3, e4 = st.columns(4)
        ex0 = e1.number_input("excl left x", 0, W, 0, key=f"ex0_{q}_{which}{ns}")
        ey0 = e2.number_input("excl top y", 0, H, 0, key=f"ey0_{q}_{which}{ns}")
        ex1 = e3.number_input("excl right x", 0, W, 0, key=f"ex1_{q}_{which}{ns}")
        ey1 = e4.number_input("excl bottom y", 0, H, 0, key=f"ey1_{q}_{which}{ns}")
        if ex1 > ex0 and ey1 > ey0:
            excl = [(ex0, ey0, ex1, ey1)]

    if not st.button(f"Extract {which.lower()} curve", key=f"go_{q}_{which}{ns}"):
        return
    try:
        curve = si.extract_curve(
            rgb, box, _hex_to_rgb(col_hex),
            ((bx0, wl_lo), (bx1, wl_hi)),
            ((by1, v_bot), (by0, v_top)),
            tolerance=tol, exclude=excl,
            wl_grid=np.arange(wl_lo, wl_hi + 1, 1.0),
        )
    except ValueError as e:
        st.error(str(e))
        return

    flats = si.find_flat_runs(curve)
    if flats:
        st.warning(
            "**Suspicious flat stretch(es) detected** — "
            + "; ".join(f"{a:.0f}–{b:.0f} nm pinned at {v:.2f}" for a, b, v in flats)
            + ". Real spectra are almost never dead flat away from zero. This is usually "
            "the legend's colour swatch being read as the curve. Tick *Exclude a region* "
            "and box in the swatch tightly, then extract again."
        )

    fig2, ax2 = plt.subplots(figsize=(9, 3))
    ax2.plot(curve[:, 0], curve[:, 1], color=col_hex, lw=1.6)
    for a, b, _ in flats:
        ax2.axvspan(a, b, color="orange", alpha=0.3)
    ax2.set_xlabel("Wavelength (nm)"); ax2.set_ylabel("Normalized")
    ax2.set_title(f"{q} — digitised {which.lower()}")
    ax2.grid(alpha=0.3)
    plt.tight_layout()
    st.pyplot(fig2)

    entry = dict(st.session_state.custom_curves.get(q, {}))
    entry.pop("fpbase_name", None)
    entry["EM" if which == "Emission" else "EX"] = curve.tolist()
    ok, err = remember_custom(q, entry, source="digitised wavelength spectrum")
    have = ", ".join(k for k in ("EM", "EX", "AB") if k in entry)
    st.success(
        f"Saved {which.lower()} for {q}. Curves held for this dye: {have}."
        + ("  \nStored in `custom_dyes/` — it will load automatically next time."
           if ok else "")
    )
    if not ok:
        st.warning(f"Held for this session only — could not write to disk: {err}")
    if "EM" not in entry:
        st.info("An **emission** curve is required before this dye can be analysed.")
    elif "EX" not in entry:
        st.info(
            "Add the **excitation** curve too — without it this dye is excluded from the "
            "laser-weighted and Cytek 64-channel modes, which are the ones worth trusting."
        )


tab1, tab2, tab3 = st.tabs(
    ["1. Fluorophores & spectra", "2. Colinearity analysis", "3. Panel recommendations"]
)

# ------------------------------------------------------------------ TAB 1 ---
with tab1:
    for prob in st.session_state.get("custom_load_problems", []):
        st.warning(f"Could not load a saved custom dye — {prob}")

    saved_meta = custom_store.metadata()
    if saved_meta:
        with st.expander(f"Saved custom dyes ({len(saved_meta)}) — reused automatically"):
            st.caption(
                "Dyes FPbase doesn't carry, kept in `custom_dyes/`. Any of these named in "
                "your list below is used without re-uploading anything."
            )
            st.dataframe(pd.DataFrame(saved_meta)[["dye", "data", "source", "saved", "note"]],
                         use_container_width=True, hide_index=True)
            dead = st.selectbox("Remove one", ["—"] + [r["dye"] for r in saved_meta],
                                key="del_custom")
            if dead != "—" and st.button(f"Delete '{dead}'", key="del_custom_btn"):
                custom_store.delete(dead)
                st.session_state.custom_curves.pop(dead, None)
                st.success(f"Deleted {dead}.")
                st.rerun()

    st.subheader("Fluorophore list")
    raw_list = st.text_area(
        "One fluorophore per line (common flow-cytometry shorthand is fine, e.g. BV421, BUV395):",
        value=EXAMPLE_LIST,
        height=260,
    )
    queries = [ln.strip() for ln in raw_list.splitlines() if ln.strip()]

    _lib_n = len(cytek_library.load())
    SRC_CYTEK = f"Cytek Aurora 5L only — {_lib_n} fluorochromes (recommended)"
    SRC_BOTH = "Cytek + FPbase (adds wavelength curves for the non-Cytek modes)"
    source_mode = st.radio(
        "Spectral source",
        [SRC_CYTEK, SRC_BOTH],
        key="source_mode",
        help="Cytek-only is the right choice for analysis on an Aurora 5L: these are the "
             "instrument's own measured 64-channel signatures, so nothing is inferred and "
             "no FPbase lookup is needed (which also makes matching instant). Add FPbase "
             "only if you want the emission-only or generic-laser modes, which need "
             "wavelength curves.",
    )
    cytek_only = source_mode == SRC_CYTEK
    use_cytek_lib = True

    colA, colB = st.columns([1, 1])
    with colA:
        refresh_index = st.checkbox("Force-refresh FPbase dye index", value=False,
                                    disabled=cytek_only)
    with colB:
        do_match = st.button("Match sources", type="primary")

    if do_match:
        st.session_state.cytek_only = cytek_only
        st.session_state.selections = {}
        if cytek_only:
            # Skip FPbase entirely: no index download, no per-dye name guessing, and no
            # chance of a wrong FPbase match quietly supplying the wavelength curves.
            st.session_state.owner_lookup = {}
            st.session_state.all_dye_names = []
            for q in queries:
                st.session_state.selections[q] = None
        else:
            with st.spinner("Loading FPbase index..."):
                index_entries = fp.get_dye_index(force_refresh=refresh_index)
                st.session_state.owner_lookup = fp.build_owner_lookup(index_entries)
                all_names = sorted(st.session_state.owner_lookup.keys())
            st.session_state.all_dye_names = all_names
            for q in queries:
                suggestions = fp.suggest_matches(q, all_names, n=5)
                confident = fp.has_confident_match(q, all_names)
                st.session_state.selections[q] = suggestions[0] if (suggestions and confident) else None
        n_cy = sum(1 for q in queries if cytek_library.find(q))
        n_missing = len(queries) - n_cy
        if cytek_only:
            st.success(
                f"Matched against Cytek's official Aurora 5L library "
                f"({len(cytek_library.load())} fluorochromes) — {n_cy} of {len(queries)} "
                "resolved."
                + (f" {n_missing} not in the library; supply those below."
                   if n_missing else "")
            )
        else:
            st.success(
                f"Matched against Cytek's 5L library ({n_cy}/{len(queries)} resolved) and "
                f"FPbase ({len(st.session_state.all_dye_names)} fluorophores) for the "
                "wavelength curves. Review below."
            )

    if "all_dye_names" in st.session_state and queries:
        cytek_only = st.session_state.get("cytek_only", True)
        st.subheader("Review matches")
        if cytek_only:
            st.caption(
                "Every dye uses Cytek's own measured 64-channel signature — nothing is "
                "inferred from reference spectra. Anything the library doesn't carry needs "
                "a signature supplied below, or it is excluded from the analysis."
            )
        else:
            st.caption(
                "The dropdown picks the **FPbase** entry, which supplies the wavelength "
                "curves. Where Cytek publishes an official 5L signature it is noted under "
                "the dye and is what the Cytek 64-channel mode actually uses — the two "
                "work together. Choose **-- not found / use custom --** to supply your own."
            )

        # Source resolution at a glance, before fetching -- otherwise the only signal
        # that Cytek is being used at all arrives after the fetch step.
        rows = []
        for q in queries:
            cy = cytek_library.find(q)
            fpn = st.session_state.selections.get(q)
            custom = q in st.session_state.custom_curves
            row = {
                "dye": q,
                "Cytek 5L signature": cy or "—",
                "your upload": "yes (overrides)" if custom else "—",
                "used for analysis": ("your upload" if custom and
                                      "SIGNATURE" in st.session_state.custom_curves[q]
                                      else "Cytek official" if cy
                                      else "computed from FPbase" if fpn and not cytek_only
                                      else "NOTHING — dye excluded"),
            }
            if not cytek_only:
                row["FPbase (wavelength curves)"] = fpn or "—"
            rows.append(row)
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        gap = [r["dye"] for r in rows if r["used for analysis"].startswith("NOTHING")]
        if gap:
            st.warning(
                f"**Not in Cytek's library: {', '.join(gap)}.** Supply a signature for "
                "each below (screenshot of a vendor channel-signature plot works), or they "
                "drop out of the analysis."
            )

        NOT_FOUND = "-- not found / use custom --"
        for q in queries:
            cy_name = cytek_library.find(q)

            if cytek_only:
                # No FPbase dropdown to show; the only decision left is what to do about
                # dyes the library lacks.
                if cy_name:
                    continue
            else:
                suggestions = fp.suggest_matches(q, st.session_state.all_dye_names, n=8)
                confident = fp.has_confident_match(q, st.session_state.all_dye_names)
                options = suggestions + [NOT_FOUND]
                current = st.session_state.selections.get(q)
                if current in options:
                    default_idx = options.index(current)
                elif suggestions and confident:
                    default_idx = 0
                else:
                    default_idx = len(options) - 1        # NOT_FOUND
                choice = st.selectbox(f"**{q}**", options, index=default_idx, key=f"match_{q}")
                st.session_state.selections[q] = None if choice == NOT_FOUND else choice
                if cy_name:
                    st.caption(
                        f":green[Cytek official 5L signature: **{cy_name}**] — this is "
                        "what the Cytek 64-channel mode will use, regardless of the "
                        "FPbase choice above."
                    )
                if not confident:
                    nearest = suggestions[0] if suggestions else None
                    extra = (f" The closest name FPbase has is `{nearest}`, which is a "
                             "spelling-similarity guess, not the same reagent."
                             if nearest else "")
                    st.caption(
                        f":orange[No confident match for **{q}** on FPbase.]{extra} "
                        "Supply the spectrum yourself below unless you recognise a "
                        "genuine match in the list."
                    )
                if cy_name:
                    continue
                needs_input = choice == NOT_FOUND

            if cytek_only:
                needs_input = True     # reached only when the library has no entry

            if needs_input:
                # keep it open once the user is working in here, otherwise every widget
                # interaction reruns the script and folds the panel shut mid-task
                open_key = f"expanded_{q}"
                with st.expander(f"Supply a spectrum for '{q}'",
                                 expanded=st.session_state.get(open_key, False)):
                    st.session_state[open_key] = True
                    if q in st.session_state.custom_curves:
                        have = ", ".join(k for k in ("SIGNATURE", "EM", "EX", "AB")
                                         if k in st.session_state.custom_curves[q])
                        st.success(f"Already saved for **{q}** ({have}) — nothing to do "
                                   "unless you want to replace it.")
                    with st.container():
                        st.markdown(f"**Find `{q}` online**")
                        st.caption(
                            "These open the vendor's own spectra viewer for this dye. "
                            "Screenshot the plot there, then load it below — prefer a "
                            "**channel signature** plot over a wavelength spectrum."
                        )
                        for s in spectra_sources.links_for(q):
                            st.markdown(
                                f"- [{s['name']}]({s['href']}) — *{s['gives']}* — "
                                f"<span style='opacity:.75'>{s['note']}</span>",
                                unsafe_allow_html=True,
                            )
                    st.divider()
                    # Screenshot first, and the default: it is the common case and the
                    # most accurate source. Having CSV first meant a PNG dropped into the
                    # CSV uploader (which took any file type) died on "'utf-8' codec
                    # can't decode byte 0x89" -- the PNG magic byte.
                    src = st.radio(
                        "Source",
                        ["Screenshot: Cytek channel signature",
                         "Screenshot: wavelength spectrum",
                         "CSV file"],
                        key=f"src_{q}",
                        help="Screenshot of a plot whose x axis reads 'Emission Channel' "
                             "(UV1…R8) → first option. Plot in nanometres → second. "
                             "Only pick CSV if you have actual numeric data.",
                    )
                    if src == "CSV file":
                        st.caption(
                            "Numeric CSV with columns: wavelength, emission, [excitation]. "
                            "Excitation is optional but required for laser-weighted / Cytek "
                            "modes. **Screenshots go in one of the options above, not here.**"
                        )
                        up = st.file_uploader(
                            f"upload_{q}", key=f"upload_{q}", label_visibility="collapsed",
                            type=["csv", "txt", "tsv"],
                        )
                        if up is not None:
                            head = up.read(8)
                            up.seek(0)
                            if head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8")):
                                st.error(
                                    "That's an image, not a CSV. Pick **Screenshot: Cytek "
                                    "channel signature** above if its x axis is UV1…R8, or "
                                    "**Screenshot: wavelength spectrum** if it's in nm."
                                )
                            else:
                                try:
                                    df = pd.read_csv(up)
                                    df.columns = [c.strip().lower() for c in df.columns]
                                    missing_cols = {"wavelength", "emission"} - set(df.columns)
                                    if missing_cols:
                                        raise ValueError(
                                            "missing column(s): "
                                            + ", ".join(sorted(missing_cols))
                                            + f". Found: {', '.join(df.columns)}"
                                        )
                                    entry = {"EM": df[["wavelength", "emission"]].values.tolist()}
                                    if "excitation" in df.columns:
                                        entry["EX"] = df[["wavelength", "excitation"]].values.tolist()
                                    ok, err = remember_custom(q, entry, source="CSV upload",
                                                              note=f"{len(df)} points")
                                    st.success(
                                        f"Loaded custom spectrum for {q} ({len(df)} points)."
                                        + ("  \nStored in `custom_dyes/` — it will load "
                                           "automatically next time." if ok else "")
                                    )
                                    if not ok:
                                        st.warning(f"Session only — disk write failed: {err}")
                                except Exception as e:
                                    st.error(f"Could not read that CSV — {e}")
                    elif src == "Screenshot: Cytek channel signature":
                        signature_digitizer_ui(q)
                    else:
                        image_digitizer_ui(q)

        # A dye that resolved against FPbase or the Cytek library never shows the
        # "not found" panel, so without this there is no way to supply your own data for
        # it -- e.g. a signature straight off your own instrument, or a newer plot than
        # the cached library has.
        with st.expander("Add or replace a spectrum for any dye in the list"):
            st.caption(
                "Use this when a dye already matched but you want to override it with "
                "your own data — a signature off your own instrument, or a newer plot "
                "than the cached library carries. What you add here takes precedence "
                "over both FPbase and the Cytek library."
            )
            target = st.selectbox("Dye", queries, key="manual_target")
            if target:
                manual_source_ui(target, key_prefix="manual")

        if st.button("Fetch spectra for matched dyes", type="primary"):
            dye_data = {}
            missing, reused, from_cytek = [], [], []
            progress = st.progress(0.0, text="Fetching...")
            items = list(st.session_state.selections.items())
            for i, (q, fp_name) in enumerate(items):
                custom = st.session_state.custom_curves.get(q)
                if fp_name is not None:
                    ids = st.session_state.owner_lookup[fp_name]
                    dye_data[q] = fp.fetch_dye_curves(fp_name, ids)
                if custom:
                    entry = dye_data.get(q, {"fpbase_name": "(custom)"})
                    entry.update({k: v for k, v in custom.items() if k != "fpbase_name"})
                    dye_data[q] = entry
                    reused.append(q)

                # Cytek's measured signature layers *on top of* whatever we have: the
                # Cytek mode prefers SIGNATURE, the wavelength modes still use EM/EX,
                # so a dye can be covered in every mode at once. An explicit upload wins
                # though -- overriding it here would make the override control useless.
                if use_cytek_lib:
                    hit = cytek_library.signature(q)
                    if hit:
                        lib_name, sig = hit
                        entry = dye_data.get(q, {"fpbase_name": f"(Cytek: {lib_name})"})
                        if not (custom and "SIGNATURE" in custom):
                            entry["SIGNATURE"] = sig.tolist()
                            entry["cytek_name"] = lib_name
                            from_cytek.append(q)
                        dye_data[q] = entry
                        if q in missing:
                            missing.remove(q)

                if q not in dye_data:
                    missing.append(q)
                progress.progress((i + 1) / len(items), text=f"Fetching... {q}")
            progress.empty()
            st.session_state.dye_data = dye_data
            if from_cytek:
                st.info(
                    f"Using Cytek's official Aurora 5L signature for {len(from_cytek)} "
                    f"dye(s): {', '.join(from_cytek)}."
                )
            if reused:
                st.info(f"Reused saved custom data for: {', '.join(reused)}.")
            if missing:
                st.warning(
                    f"No spectrum available for: {', '.join(missing)}. They will be "
                    "excluded — supply a CSV or screenshot above to include them."
                )
            no_curves = [n for n, e in dye_data.items() if "EM" not in e]
            if no_curves:
                st.caption(
                    f"Signature-only (Cytek 5L mode only, no wavelength curves): "
                    f"{', '.join(no_curves)}."
                )
            st.success(f"Spectra ready for {len(dye_data)} dyes. Go to the **Colinearity analysis** tab.")

# ------------------------------------------------------------------ TAB 2 ---
with tab2:
    dye_data = st.session_state.dye_data
    if not dye_data:
        st.info("Fetch spectra in Tab 1 first.")
    elif len(dye_data) < 2:
        st.warning(
            f"Only **{len(dye_data)}** dye has data ({', '.join(dye_data)}). Colinearity "
            "is a property of *pairs*, so at least two are needed. Add more dyes in Tab 1, "
            "or supply data for the ones that were excluded."
        )
    else:
        st.subheader("Instrument / weighting configuration")
        _cytek_preset = "Cytek Aurora / Northern Lights 5L (355/405/488/561/640)"
        if st.session_state.get("cytek_only", True):
            st.info(
                "**Cytek Aurora 5L, 64 detector channels** — using the instrument's own "
                "measured signatures. The other modes need wavelength curves, which "
                "Cytek-only data doesn't carry; switch the spectral source in Tab 1 to "
                "*Cytek + FPbase* if you want them."
            )
            preset_name = _cytek_preset
        else:
            preset_name = st.selectbox(
                "Mode", list(LASER_PRESETS.keys()),
                index=list(LASER_PRESETS.keys()).index(_cytek_preset),
            )
        preset = LASER_PRESETS[preset_name]

        lasers = None
        if preset == "custom":
            txt = st.text_input("Laser lines, nm (comma-separated)", value="405,488,640")
            try:
                wavelengths = [float(x.strip()) for x in txt.split(",") if x.strip()]
                lasers = {f"L{int(w)}": w for w in wavelengths}
            except ValueError:
                st.error("Could not parse laser wavelengths.")
        elif preset is not None:
            lasers = preset

        if st.session_state.get("cytek_only", True):
            use_cytek_channels = True
        else:
            use_cytek_channels = st.checkbox(
                "Bin into Cytek Aurora 5L's actual 64 detector channels "
                "(requires the 5L preset above)",
                value=(preset_name.startswith("Cytek")),
            )

        # Compute automatically the first time this tab is opened for a given panel and
        # mode. Requiring a button click here meant landing on the tab after fetching
        # showed nothing at all, which reads as "the second tab is empty".
        fingerprint = (tuple(sorted(dye_data)), preset_name, bool(use_cytek_channels))
        stale = st.session_state.get("results_fingerprint") != fingerprint
        run = st.button("Recompute", type="primary") or stale
        if stale:
            st.session_state.results_fingerprint = fingerprint

        sig_only = [n for n, e in dye_data.items() if "SIGNATURE" in e]
        is_cytek_mode = use_cytek_channels and preset_name.startswith("Cytek")
        if sig_only and not is_cytek_mode:
            st.warning(
                f"**{', '.join(sig_only)}** was supplied as an Aurora 5L channel "
                "signature, which has no wavelength curves behind it. It can only be "
                "analysed in the **Cytek Aurora 5L** mode with channel binning ticked — "
                "in this mode it will be dropped."
            )

        if run:
            em_curves, ex_curves = col.build_curves(dye_data)
            n_no_ex = [n for n in em_curves if n not in ex_curves]

            if lasers is None:
                pearson, cosine = col.emission_only_similarity(em_curves)
                mode_label = "Emission-shape-only similarity"
                extra = {}
            elif use_cytek_channels and set(lasers.keys()) == set(LASER_PRESETS["Cytek Aurora / Northern Lights 5L (355/405/488/561/640)"].keys()):
                # Must go through rec.cytek_signature, not col.cytek64_similarity: the
                # latter derives everything from wavelength curves, so a dye carrying a
                # ready-made SIGNATURE (Cytek library, or a digitised plot) contributed
                # nothing and the matrix came out 0x0 -- a heatmap with a title and a
                # colourbar but no cells.
                sig_norm = rec.cytek_signature(dye_data)
                if len(sig_norm) < 2:
                    st.error(
                        "Fewer than two dyes have usable data for this mode, so there are "
                        "no pairs to compare. Check the source table in Tab 1."
                    )
                    st.stop()
                cosine = rec.cosine_matrix(sig_norm)
                pearson = pd.DataFrame(
                    np.corrcoef(sig_norm.values),
                    index=sig_norm.index, columns=sig_norm.index,
                )
                peak_channel = sig_norm.idxmax(axis=1)
                mode_label = "Cytek Aurora 5L 64-channel signature similarity"
                extra = {"signature": sig_norm, "peak_channel": peak_channel}
            else:
                pearson, cosine, laser_eff = col.laser_weighted_similarity(em_curves, ex_curves, lasers)
                mode_label = f"Laser-weighted similarity ({', '.join(f'{k}={v}nm' for k, v in lasers.items())})"
                extra = {"laser_eff": laser_eff}

            if n_no_ex and lasers is not None:
                st.warning(
                    f"No excitation data for: {', '.join(n_no_ex)} -- excluded from laser-weighted result."
                )

            st.session_state.results = dict(
                pearson=pearson, cosine=cosine, mode_label=mode_label, extra=extra
            )

        if "results" in st.session_state:
            res = st.session_state.results
            pearson, cosine, mode_label, extra = res["pearson"], res["cosine"], res["mode_label"], res["extra"]

            st.subheader(mode_label)
            fig, ax = plt.subplots(figsize=(0.55 * len(pearson) + 3, 0.5 * len(pearson) + 3))
            sns.heatmap(
                pearson, cmap="rocket_r", vmin=-0.3, vmax=1.0, square=True,
                linewidths=0.4, linecolor="white", annot=True, fmt=".2f",
                annot_kws={"size": 7}, cbar_kws={"label": "Pearson r"}, ax=ax,
            )
            ax.set_title(mode_label, fontsize=11)
            plt.xticks(rotation=45, ha="right", fontsize=8)
            plt.yticks(fontsize=8)
            plt.tight_layout()
            st.pyplot(fig)

            png_buf = io.BytesIO()
            fig.savefig(png_buf, format="png", dpi=200)
            st.download_button("Download heatmap PNG", png_buf.getvalue(), "colinearity_heatmap.png", "image/png")

            if "peak_channel" in extra:
                st.caption("Peak detector channel per dye:")
                st.dataframe(extra["peak_channel"].rename("peak_channel"))

            st.subheader("Ranked pairwise similarity")
            pairs_df = col.ranked_pairs(pearson, cosine)
            st.dataframe(pairs_df, use_container_width=True, height=350)

            c1, c2, c3 = st.columns(3)
            with c1:
                st.download_button(
                    "Download pairwise CSV", pairs_df.to_csv(index=False), "pairwise_similarity_ranked.csv", "text/csv"
                )
            with c2:
                st.download_button(
                    "Download full correlation matrix CSV", pearson.to_csv(), "pearson_correlation.csv", "text/csv"
                )
            with c3:
                if "signature" in extra:
                    st.download_button(
                        "Download 64-channel signature CSV",
                        extra["signature"].to_csv(),
                        "cytek64_normalized_signature.csv",
                        "text/csv",
                    )

# ------------------------------------------------------------------ TAB 3 ---
with tab3:
    dye_data = st.session_state.dye_data
    if not dye_data:
        st.info("Fetch spectra in Tab 1 first.")
    elif len(dye_data) < 2:
        st.warning(
            f"Only **{len(dye_data)}** dye has data. Swap recommendations are scored "
            "against the rest of the panel, so at least two dyes are needed."
        )
    else:
        st.subheader("Suggested swaps to reduce colinearity")
        st.warning(
            "**These are spectral suggestions only -- they are not panel design.** "
            "Before acting on any of them, check that (1) the reagent actually exists "
            "conjugated to your clone, (2) the dye's brightness suits that marker's "
            "expression level (dim dyes on high-expression markers, bright dyes on low), "
            "and (3) the markers involved are actually co-expressed on the same cells -- "
            "two colinear dyes on mutually exclusive populations rarely matter. "
            "Spectral similarity is one input to panel design, not the objective function."
        )

        panel_fpnames = {k: v.get("fpbase_name", k) for k, v in dye_data.items()}
        sig = rec.cytek_signature(dye_data)
        slots = rec.worst_partner_per_slot(sig)
        base = rec.panel_metrics(sig)

        c1, c2, c3 = st.columns(3)
        c1.metric("Worst pair (cosine)", f"{base['max']:.3f}",
                  help=" / ".join(base['worst_pair']) if base['worst_pair'] else "no pairs")
        c2.metric("Pairs > 0.5", base["n_over_0.5"])
        c3.metric("Pairs > 0.4", base["n_over_0.4"])

        st.caption("Worst partner for each dye in the current panel:")
        st.dataframe(slots, use_container_width=True, height=250)

        st.divider()
        slot = st.selectbox(
            "Which dye would you consider replacing?",
            list(slots["dye"]),
            help="Slots are listed worst-first.",
        )
        cA, cB = st.columns(2)
        with cA:
            viability_slot = st.checkbox(
                "This is the viability dye (no antibody attached)",
                value=("zombie" in slot.lower() or "live" in slot.lower()
                       or "viability" in slot.lower()),
                help="Viability dyes can move anywhere in the spectrum, so the same-laser "
                     "restriction is lifted and only viability reagents are proposed. "
                     "Usually the cheapest fix in a panel.",
            )
        with cB:
            same_laser = st.checkbox(
                "Restrict to the same laser (like-for-like)",
                value=True,
                help="Keeps the marker's brightness tier and reagent availability plausible. "
                     "Unchecking will suggest spectrally-optimal dyes that may not exist for "
                     "your clone.",
                disabled=viability_slot,
            )

        if st.button("Find replacement candidates", type="primary"):
            with st.spinner("Fetching candidate reagent spectra from FPbase (cached after first run)..."):
                prog = st.progress(0.0)
                pool = fp.fetch_candidate_pool(
                    cand_mod.ALL_CANDIDATES,
                    progress_cb=lambda f, n: prog.progress(min(f, 1.0), text=f"fetching {n}"),
                )
                prog.empty()
            st.session_state.rec_result = (
                slot,
                rec.recommend_for_slot(
                    slot, dye_data, pool, panel_fpnames,
                    same_laser_only=same_laser, viability_slot=viability_slot,
                ),
            )

        if "rec_result" in st.session_state:
            rslot, df = st.session_state.rec_result
            st.markdown(f"**Candidates to replace `{rslot}`**")
            if df.empty:
                st.info(
                    "No candidate in the curated reagent pool fits those constraints. "
                    "Try unchecking the same-laser restriction."
                )
            else:
                show = df.rename(columns={
                    "slot_worst_after": "worst sim. after",
                    "slot_improvement": "improvement",
                    "new_worst_partner": "new worst partner",
                    "panel_max_after": "panel max after",
                    "panel_pairs_over_0.5": "panel pairs >0.5",
                    "panel_pairs_over_0.4": "panel pairs >0.4",
                })
                st.dataframe(show, use_container_width=True, height=380)
                st.caption(
                    f"Current panel for reference: worst pair {base['max']:.3f}, "
                    f"{base['n_over_0.5']} pairs >0.5, {base['n_over_0.4']} pairs >0.4. "
                    "Prefer a candidate that lowers **both** the worst pair and the pair "
                    "counts -- a swap that minimises the single worst pair can raise the "
                    "number of moderately-correlated pairs and leave the panel worse overall."
                )
                st.download_button(
                    "Download candidate ranking CSV",
                    df.to_csv(index=False),
                    f"swap_candidates_{rslot.replace(' ', '_')}.csv",
                    "text/csv",
                )

                st.divider()
                st.subheader("Preview a swap")
                st.caption(
                    "Colinearity matrices are cosine similarity of the Cytek 5L 64-channel "
                    "signatures -- the same measure the candidate ranking above is scored on."
                )
                pick = st.selectbox(
                    f"Replace `{rslot}` with:",
                    list(df["candidate"]),
                    index=0,
                    key="swap_preview_pick",
                )

                pool = fp.fetch_candidate_pool(cand_mod.ALL_CANDIDATES)
                after_entries = rec.apply_swap(dye_data, rslot, pick, pool)
                sig_after = rec.cytek_signature(after_entries)
                m_after = rec.panel_metrics(sig_after)

                d1, d2, d3 = st.columns(3)
                d1.metric(
                    "Worst pair (cosine)", f"{m_after['max']:.3f}",
                    delta=f"{m_after['max'] - base['max']:+.3f}",
                    delta_color="inverse",
                    help=(" / ".join(m_after['worst_pair'])
                          if m_after['worst_pair'] else "no pairs"),
                )
                d2.metric(
                    "Pairs > 0.5", m_after["n_over_0.5"],
                    delta=m_after["n_over_0.5"] - base["n_over_0.5"],
                    delta_color="inverse",
                )
                d3.metric(
                    "Pairs > 0.4", m_after["n_over_0.4"],
                    delta=m_after["n_over_0.4"] - base["n_over_0.4"],
                    delta_color="inverse",
                )
                d_max = m_after["max"] - base["max"]
                d_04 = m_after["n_over_0.4"] - base["n_over_0.4"]
                d_05 = m_after["n_over_0.5"] - base["n_over_0.5"]
                if d_max < -1e-9 and (d_04 > 0 or d_05 > 0):
                    st.warning(
                        f"**Mixed result -- read past the headline number.** This swap lowers "
                        f"the single worst pair ({d_max:+.3f}) but *increases* the number of "
                        f"moderately correlated pairs (>0.4: {d_04:+d}, >0.5: {d_05:+d}). "
                        "The replacement is spreading its overlap across more of the panel "
                        "instead of concentrating it in one pair. A swap like this usually "
                        "makes unmixing harder overall, not easier."
                    )
                elif d_max >= -1e-9 and d_04 >= 0 and d_05 >= 0:
                    st.info(
                        "This swap does not improve the panel -- the worst pair lies elsewhere, "
                        "so changing this slot cannot move the headline number. Try the slot "
                        "named in the worst-pair metric above."
                    )

                cos_before = rec.cosine_matrix(sig)
                cos_after = rec.cosine_matrix(sig_after)
                lab_before = [rec.short_label(n) for n in cos_before.index]
                lab_after = [rec.short_label(n) for n in cos_after.index]

                fig3, axes3 = plt.subplots(
                    1, 2, figsize=(2 * (0.5 * len(cos_before) + 3), 0.5 * len(cos_before) + 3)
                )
                for ax, mat, labs, ttl in zip(
                    axes3,
                    [cos_before, cos_after],
                    [lab_before, lab_after],
                    [f"Before  (worst {base['max']:.3f})",
                     f"After: {rslot} → {pick}  (worst {m_after['max']:.3f})"],
                ):
                    sns.heatmap(
                        mat, cmap="rocket_r", vmin=0.0, vmax=1.0, square=True,
                        linewidths=0.4, linecolor="white", annot=True, fmt=".2f",
                        annot_kws={"size": 6}, cbar_kws={"label": "cosine similarity"},
                        xticklabels=labs, yticklabels=labs, ax=ax,
                    )
                    ax.set_title(ttl, fontsize=10)
                    ax.tick_params(axis="x", rotation=45, labelsize=7)
                    ax.tick_params(axis="y", rotation=0, labelsize=7)
                    for lbl in ax.get_xticklabels():
                        lbl.set_ha("right")
                    # outline the row/column of the dye that changed
                    if labs is lab_after:
                        k = list(cos_after.index).index(pick)
                        for rect in (
                            plt.Rectangle((k, 0), 1, len(cos_after), fill=False, ec="#1f77b4", lw=2),
                            plt.Rectangle((0, k), len(cos_after), 1, fill=False, ec="#1f77b4", lw=2),
                        ):
                            ax.add_patch(rect)
                plt.tight_layout()
                st.pyplot(fig3)

                buf3 = io.BytesIO()
                fig3.savefig(buf3, format="png", dpi=200)
                e1, e2 = st.columns(2)
                with e1:
                    st.download_button(
                        "Download before/after heatmap PNG",
                        buf3.getvalue(),
                        f"swap_{rslot.replace(' ', '_')}_to_{pick.replace(' ', '_')}.png",
                        "image/png",
                    )
                with e2:
                    st.download_button(
                        "Download post-swap matrix CSV",
                        cos_after.to_csv(),
                        f"cosine_after_swap_{pick.replace(' ', '_')}.csv",
                        "text/csv",
                    )
