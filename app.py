"""
Fluorophore Colinearity Explorer
---------------------------------
Streamlit GUI for spectral overlap of a flow panel.

The screen draws peak-normalized Aurora 5L signatures, brings the closest
pair forward, and scores that pair with cosine (the similarity index) and
Pearson. Wavelength curves are a second view, used when FPbase spectra are
loaded. Swap suggestions stay spectral suggestions, not panel design.

Run with:  streamlit run app.py
"""
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import streamlit as st

import fpbase_client as fp
import colinearity as col
import recommend as rec
import candidates as cand_mod
import spectra_image as si
import spectra_view as sv
import custom_store
import cytek_library
import spectra_sources
from cytek_channels import LASER_PRESETS

st.set_page_config(
    page_title="Panel overlap",
    page_icon=":material/show_chart:",
    layout="wide",
)
st.title("Panel overlap", icon=":material/show_chart:")
st.caption(
    "How alike each pair of fluorophores looks on an Aurora 5L. "
    "A higher cosine means the two channel patterns are harder to tell apart."
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


CYTEK_PRESET = "Cytek Aurora / Northern Lights 5L (355/405/488/561/640)"
NOT_FOUND = "-- not found / use custom --"
SCORE_NOTE = (
    "Cosine is how alike the two channel patterns are. "
    "Pearson is whether they rise and fall together. These lines are the patterns both numbers use."
)
SCALE_NOTE = (
    "On Cytek's scale, signatures are usually still separable until about 0.98. "
    "Pairs above 0.4 and 0.5 are the closer pairs in this panel, and they are how a swap is judged. "
    "They are not a do-not-combine line. Co-expression matters more than the number."
)
SWAP_WARNING = (
    "**These are spectral suggestions only. They are not panel design.** "
    "Before acting on any of them, check that (1) the reagent actually exists "
    "conjugated to your clone, (2) the dye's brightness suits that marker's "
    "expression level (dim dyes on high-expression markers, bright dyes on low), "
    "and (3) the markers involved are actually co-expressed on the same cells. "
    "Two colinear dyes on mutually exclusive populations rarely matter. "
    "Spectral similarity is one input to panel design, not the objective function."
)


def _theme() -> dict:
    context = getattr(st, "context", None)
    theme = getattr(context, "theme", None)
    if theme is not None and getattr(theme, "type", "light") == "dark":
        return {"ink": "#e6edf3", "muted": "#9aa6b2", "grid": "#3a4654", "diag": "#2c3542"}
    return {"ink": "#1b2430", "muted": "#5c6b7a", "grid": "#d5dde6", "diag": "#e7ebf0"}


def _remember(token: str, key: str) -> bool:
    """True only when a widget selection changes after the first run."""
    previous = st.session_state.get(key)
    st.session_state[key] = token
    return previous is not None and token != previous


def _cell_pair(selection) -> tuple[str, str] | None:
    if not selection:
        return None
    try:
        payload = selection["cell"]
    except Exception:
        return None
    if not payload:
        return None
    if isinstance(payload, (list, tuple)):
        if not payload or not isinstance(payload[0], dict):
            return None
        item = payload[0]
        a, b = item.get("dye_x"), item.get("dye_y")
    else:
        try:
            xs, ys = payload["dye_x"], payload["dye_y"]
        except Exception:
            return None
        if not xs or not ys:
            return None
        a, b = xs[0], ys[0]
    if not a or not b or a == b:
        return None
    return a, b


def resolve_panel(queries: list[str], cytek_only: bool, refresh_index: bool):
    """Match names and load signatures. FPbase is contacted only in wavelength mode."""
    st.session_state.selections = {}
    if cytek_only:
        st.session_state.owner_lookup = {}
        st.session_state.all_dye_names = []
        for query in queries:
            st.session_state.selections[query] = None
    else:
        index_entries = fp.get_dye_index(force_refresh=refresh_index)
        st.session_state.owner_lookup = fp.build_owner_lookup(index_entries)
        all_names = sorted(st.session_state.owner_lookup)
        st.session_state.all_dye_names = all_names
        for query in queries:
            suggestions = fp.suggest_matches(query, all_names, n=5)
            confident = fp.has_confident_match(query, all_names)
            st.session_state.selections[query] = (
                suggestions[0] if suggestions and confident else None
            )

    dye_data: dict = {}
    missing: list[str] = []
    reused: list[str] = []
    from_cytek: list[str] = []
    items = list(st.session_state.selections.items())
    progress = st.progress(0.0, text="Loading wavelength curves") if not cytek_only else None
    for i, (query, fp_name) in enumerate(items):
        custom = st.session_state.custom_curves.get(query)
        if fp_name is not None:
            ids = st.session_state.owner_lookup[fp_name]
            dye_data[query] = fp.fetch_dye_curves(fp_name, ids)
        if custom:
            entry = dye_data.get(query, {"fpbase_name": "(custom)"})
            entry.update({k: v for k, v in custom.items() if k != "fpbase_name"})
            dye_data[query] = entry
            reused.append(query)
        hit = cytek_library.signature(query)
        if hit:
            lib_name, signature = hit
            entry = dye_data.get(query, {"fpbase_name": f"(Cytek: {lib_name})"})
            if not (custom and "SIGNATURE" in custom):
                entry["SIGNATURE"] = signature.tolist()
                entry["cytek_name"] = lib_name
                from_cytek.append(query)
            dye_data[query] = entry
        if query not in dye_data:
            missing.append(query)
        if progress is not None:
            progress.progress((i + 1) / max(len(items), 1), text=f"Loading {query}")
    if progress is not None:
        progress.empty()
    return dye_data, missing, reused, from_cytek


def compute_view(dye_data: dict, preset_name: str, use_channels: bool, custom_txt: str) -> dict:
    """Pearson and cosine for the selected instrument. Does not draw anything."""
    warnings: list[str] = []
    em_curves, ex_curves = col.build_curves(dye_data)
    preset = LASER_PRESETS[preset_name]
    if preset == "custom":
        try:
            wavelengths = [float(part.strip()) for part in (custom_txt or "").split(",") if part.strip()]
        except ValueError:
            return {"error": "Could not parse laser wavelengths. Use comma-separated numbers, such as 405, 488, 640."}
        if not wavelengths:
            return {"error": "Enter at least one laser wavelength."}
        lasers = {f"L{int(wave)}": wave for wave in wavelengths}
    else:
        lasers = preset

    cytek_lasers = LASER_PRESETS[CYTEK_PRESET]
    try:
        if lasers is None:
            if len(em_curves) < 2:
                return {"error": "Fewer than two dyes have wavelength curves, so there is no emission-shape comparison."}
            pearson, cosine = col.emission_only_similarity(em_curves)
            mode, label = "emission", "Emission shape"
            extra = {"em": em_curves, "ex": ex_curves}
        elif use_channels and set(lasers) == set(cytek_lasers):
            signature = rec.cytek_signature(dye_data)
            if len(signature) < 2:
                return {"error": "Fewer than two dyes have usable Aurora signatures, so there are no pairs to compare."}
            cosine = rec.cosine_matrix(signature)
            pearson = pd.DataFrame(
                np.corrcoef(signature.values), index=signature.index, columns=signature.index
            )
            mode, label = "cytek", "Aurora 5L signatures"
            extra = {
                "signature": signature,
                "em": em_curves,
                "ex": ex_curves,
                "peak_channel": signature.idxmax(axis=1),
            }
            dropped = [name for name in dye_data if name not in signature.index]
            if dropped:
                warnings.append(
                    "Not in this Aurora comparison: "
                    + ", ".join(dropped)
                    + ". A dye needs a 64-channel signature, or both an emission curve and an excitation curve."
                )
        else:
            usable = [name for name in em_curves if name in ex_curves]
            if len(usable) < 2:
                return {"error": "Fewer than two dyes have both emission and excitation curves for this laser set."}
            pearson, cosine, laser_eff = col.laser_weighted_similarity(em_curves, ex_curves, lasers)
            phrase = ", ".join(f"{wave:g} nm" for wave in lasers.values())
            mode, label = "laser", f"Laser-weighted emission ({phrase})"
            extra = {"em": em_curves, "ex": ex_curves, "lasers": lasers, "laser_eff": laser_eff}
            no_ex = [name for name in em_curves if name not in ex_curves]
            if no_ex:
                warnings.append(
                    "No excitation curve for: "
                    + ", ".join(no_ex)
                    + ". They are excluded from this laser-weighted result."
                )
            signature_only = [name for name in dye_data if name not in em_curves]
            if signature_only:
                warnings.append(
                    "Signature only, so not on this wavelength plot: " + ", ".join(signature_only) + "."
                )
    except ValueError as exc:
        return {"error": str(exc)}

    return {
        "error": None,
        "warnings": warnings,
        "pearson": pearson.fillna(0.0),
        "cosine": cosine.fillna(0.0),
        "mode": mode,
        "label": label,
        "extra": extra,
    }


def dye_status_frame(queries, dye_data, panel_sig) -> pd.DataFrame:
    rows = []
    custom = st.session_state.custom_curves
    for query in queries:
        entry = dye_data.get(query)
        peak, laser = "—", "—"
        if panel_sig is not None and query in panel_sig.index:
            peak = str(panel_sig.loc[query].idxmax())
            laser = sv.laser_label(peak)
        if entry is None:
            source = "No Aurora signature"
        elif query in custom and "SIGNATURE" in custom[query]:
            source = "Your upload"
        elif entry.get("cytek_name"):
            source = "Cytek library"
        elif "EM" in entry:
            source = "Wavelength curves"
        elif "SIGNATURE" in entry:
            source = "Signature"
        else:
            source = "No Aurora signature"
        rows.append({"Dye": query, "Peak": peak, "Laser": laser, "Source": source})
    return pd.DataFrame(rows)


def pair_frame(pearson, cosine, signature, selected, score: str) -> pd.DataFrame:
    ranked = col.ranked_pairs(pearson, cosine)
    metric = "pearson_r" if score == "Pearson" else "cosine_sim"
    ranked = ranked.sort_values(metric, ascending=False).reset_index(drop=True)
    shared, shown = [], []
    for row in ranked.itertuples(index=False):
        if (
            signature is not None
            and row.dye_1 in signature.index
            and row.dye_2 in signature.index
        ):
            runs = sv.shared_runs(signature.loc[row.dye_1], signature.loc[row.dye_2])
            shared.append(", ".join(runs) if runs else "low tail")
        else:
            shared.append("")
        shown.append("Yes" if sv.same_pair(selected, row.dye_1, row.dye_2) else "")
    frame = pd.DataFrame(
        {
            "Dye 1": ranked.dye_1,
            "Dye 2": ranked.dye_2,
            "Cosine": ranked.cosine_sim,
            "Pearson": ranked.pearson_r,
            "Shared channels": shared,
            "On plot": shown,
        }
    )
    if signature is None:
        frame = frame.drop(columns=["Shared channels"])
    return frame


def unresolved_panel(query: str):
    st.markdown(f"**Find `{query}` online**")
    st.caption(
        "These open the vendor spectra viewer for this dye. "
        "Screenshot the plot, then load it below. Prefer a channel-signature plot. "
        "Its x-axis reads UV1 through R8."
    )
    for source in spectra_sources.links_for(query):
        st.markdown(f"- [{source['name']}]({source['href']}). {source['gives']}. {source['note']}")
    manual_source_ui(query, key_prefix="missing")
    st.caption("Choose Analyze panel again after the spectrum is saved.")


def _hero_caption(view, pair) -> str:
    pearson, cosine = view["pearson"], view["cosine"]
    a, b = pair
    c_val = float(cosine.loc[a, b])
    p_val = float(pearson.loc[a, b])
    if view["mode"] == "cytek":
        return sv.pair_caption(a, b, view["extra"]["signature"], c_val, p_val)
    if view["mode"] == "emission":
        text = (
            f"{a} and {b}. Cosine {c_val:.2f}. Pearson {p_val:.2f}. "
            "These are peak-normalized emission shapes. Lasers are not in this score."
        )
        ex = view["extra"].get("ex") or {}
        if a in ex or b in ex:
            text += " Dashed lines are excitation."
        return text
    return (
        f"{a} and {b}. Cosine {c_val:.2f}. Pearson {p_val:.2f}. "
        "Each panel is emission multiplied by excitation at that laser."
    )


def _draw_hero(view, pair, show_rest: bool, ink: str, muted: str, grid: str):
    colors = st.session_state.colors
    names = list(view["cosine"].index)
    others = [name for name in names if name not in pair]
    shown = list(pair) + (others if show_rest else [])
    if view["mode"] == "cytek":
        signature = view["extra"]["signature"]
        styles = sv.pair_styles(pair, names, show_rest)
        chart = sv.signature_chart(
            signature.loc[list(styles)],
            styles,
            colors,
            label_dyes=list(pair),
            ink=ink,
            muted=muted,
            grid=grid,
        )
        st.altair_chart(chart, theme=None)
        return
    em, ex = view["extra"]["em"], view["extra"]["ex"]
    dyes = [name for name in shown if name in em]
    missing = [name for name in pair if name not in em]
    if missing:
        st.caption(
            ", ".join(missing)
            + " has an Aurora signature and no wavelength curve, so it is not on this plot."
        )
    if view["mode"] == "emission":
        chart = sv.emission_chart(em, ex, dyes, colors, ink=ink, show_lasers=False)
    else:
        chart = sv.laser_weighted_chart(em, ex, view["extra"]["lasers"], dyes, colors, ink=ink)
    if chart is None:
        st.caption("Choose a pair in the list or on the matrix. Those two signatures draw here.")
    else:
        st.altair_chart(chart, theme=None)


def _downloads(view, pairs: pd.DataFrame):
    with st.expander("Download", icon=":material/download:"):
        with st.container(horizontal=True):
            st.download_button(
                "Pair list",
                pairs.to_csv(index=False),
                "pairwise_similarity.csv",
                "text/csv",
                key="dl_pairs",
            )
            st.download_button(
                "Cosine matrix",
                view["cosine"].to_csv(),
                "cosine_similarity.csv",
                "text/csv",
                key="dl_cosine",
            )
            st.download_button(
                "Pearson matrix",
                view["pearson"].to_csv(),
                "pearson_correlation.csv",
                "text/csv",
                key="dl_pearson",
            )
            if "signature" in view["extra"]:
                st.download_button(
                    "64-channel signatures",
                    view["extra"]["signature"].to_csv(),
                    "aurora_signatures.csv",
                    "text/csv",
                    key="dl_sig",
                )


def _swap_overlay(panel_sig, slot, pick, partner, new_partner, pool):
    candidate = rec.cytek_signature({pick: pool[pick]})
    rows = {slot: panel_sig.loc[slot], pick: candidate.loc[pick]}
    styles = {
        slot: {"opacity": 1.0, "width": 2.6, "dash": "solid"},
        pick: {"opacity": 1.0, "width": 2.6, "dash": "dash"},
    }
    if partner in panel_sig.index and partner not in rows:
        rows[partner] = panel_sig.loc[partner]
        styles[partner] = {"opacity": 0.35, "width": 1.6, "dash": "solid"}
    if (
        new_partner
        and new_partner != partner
        and new_partner in panel_sig.index
        and new_partner not in rows
    ):
        rows[new_partner] = panel_sig.loc[new_partner]
        styles[new_partner] = {"opacity": 0.35, "width": 1.6, "dash": "dot"}
    frame = pd.DataFrame(rows).T
    frame = frame.reindex(columns=panel_sig.columns)
    colors = sv.assign_colors(list(frame.index), st.session_state.colors)
    st.session_state.colors = colors
    theme = _theme()
    st.altair_chart(
        sv.signature_chart(
            frame,
            styles,
            colors,
            label_dyes=[slot, pick],
            ink=theme["ink"],
            muted=theme["muted"],
            grid=theme["grid"],
        ),
        theme=None,
    )
    bits = [f"{slot} is solid.", f"{pick} is dashed."]
    if partner in styles and partner != slot:
        bits.append(f"{partner} is the current closest partner, drawn lighter.")
    if new_partner and new_partner != partner and new_partner in styles:
        bits.append(f"{new_partner} is the closest partner after the swap, drawn lighter.")
    st.caption(" ".join(bits))


# ---------------------------------------------------------------------------
# Page. Fast controls render first. Loading signatures happens on the button.
# ---------------------------------------------------------------------------

for problem in st.session_state.get("custom_load_problems", []):
    st.warning(f"Could not load a saved custom dye. {problem}")

left, right = st.columns([1, 2.35], gap="large")
with left:
    st.subheader("Dyes")
    raw_list = st.text_area(
        "Fluorophores, one per line",
        value=EXAMPLE_LIST,
        height=220,
        key="dye_list",
        help="Flow shorthand is fine: BV421, BUV395, PE-Cy7. The list in the box is an example.",
    )
    queries = list(dict.fromkeys(line.strip() for line in raw_list.splitlines() if line.strip()))
    source = st.radio(
        "Spectral source",
        ["Aurora signatures", "Also load wavelength curves"],
        key="source_choice",
        help=(
            "Aurora signatures are the instrument's measured 64-channel library and load locally. "
            "Wavelength curves come from FPbase and add emission-shape and generic-laser views."
        ),
    )
    cytek_only = source != "Also load wavelength curves"
    refresh_index = False
    with st.expander("Advanced", icon=":material/tune:"):
        refresh_index = st.checkbox(
            "Force-refresh FPbase dye index",
            value=False,
            disabled=cytek_only,
            key="refresh_index",
        )
        saved_meta = custom_store.metadata()
        if saved_meta:
            st.caption("Saved dyes are reused when you analyze a list that names them.")
            st.dataframe(
                pd.DataFrame(saved_meta)[["dye", "data", "source", "saved", "note"]],
                hide_index=True,
            )
            dead = st.selectbox(
                "Remove a saved dye",
                ["—"] + [row["dye"] for row in saved_meta],
                key="del_custom",
            )
            if dead != "—" and st.button(f"Delete {dead}", key="del_custom_btn"):
                custom_store.delete(dead)
                st.session_state.custom_curves.pop(dead, None)
                st.toast(f"Deleted {dead}")
                st.rerun()
        else:
            st.caption("No saved custom dyes yet.")
    analyze = st.button(
        "Analyze panel",
        type="primary",
        icon=":material/query_stats:",
        key="analyze",
    )
    analyzed = st.session_state.get("analyzed_queries")
    dirty = analyzed is not None and (
        queries != analyzed or cytek_only != st.session_state.get("analyzed_cytek_only")
    )
    if dirty:
        st.caption("The list or the source has changed. Analyze panel to update the plot.")
    status_slot = st.container()

with right:
    right_box = st.container()

if analyze:
    if len(queries) < 2:
        st.session_state.dye_data = {}
        st.session_state.missing_dyes = []
        st.session_state.analyzed_queries = queries
        st.session_state.analyzed_cytek_only = cytek_only
        st.error("Add at least two dyes. Overlap is a property of a pair.")
    else:
        dye_data, missing, reused, from_cytek = resolve_panel(
            queries, cytek_only, bool(refresh_index) and not cytek_only
        )
        st.session_state.dye_data = dye_data
        st.session_state.missing_dyes = missing
        st.session_state.analyzed_queries = queries
        st.session_state.analyzed_cytek_only = cytek_only
        st.session_state.colors = sv.assign_colors(
            list(dye_data), st.session_state.get("colors") or {}
        )
        st.session_state.selected_pair = None
        if from_cytek:
            st.toast(f"Aurora signatures for {len(from_cytek)} dyes")
        if reused:
            st.toast("Reused saved spectra for " + ", ".join(reused))
        if not cytek_only:
            no_curves = [name for name, entry in dye_data.items() if "EM" not in entry]
            if no_curves:
                st.caption(
                    "No wavelength curve for: "
                    + ", ".join(no_curves)
                    + ". They stay on the Aurora plot and drop out of the wavelength views."
                )

dye_data = st.session_state.dye_data
panel_sig = None
if dye_data:
    try:
        panel_sig = rec.cytek_signature(dye_data)
        if len(panel_sig) == 0:
            panel_sig = None
    except ValueError as exc:
        st.error(str(exc))

with status_slot:
    if dye_data:
        st.dataframe(
            dye_status_frame(st.session_state.get("analyzed_queries") or queries, dye_data, panel_sig),
            hide_index=True,
            height=360,
        )

with right_box:
    if not dye_data:
        analyzed_queries = st.session_state.get("analyzed_queries")
        if not analyzed_queries:
            st.markdown("Nothing plotted yet. Add at least two dyes and choose **Analyze panel**.")
        elif len(analyzed_queries) < 2:
            st.markdown("Add at least two dyes. Overlap is a property of a pair.")
        else:
            st.markdown("No spectra were loaded for this list. Add a signature for each dye below.")
    else:
        preset_name = CYTEK_PRESET
        use_channels = True
        custom_txt = "405, 488, 640"
        if not st.session_state.get("analyzed_cytek_only", True):
            with st.expander("Other instruments", icon=":material/tune:"):
                st.caption("The Aurora channel plot stays available. These options score wavelength curves instead.")
                preset_names = list(LASER_PRESETS)
                preset_name = st.selectbox(
                    "Instrument",
                    preset_names,
                    index=preset_names.index(CYTEK_PRESET),
                    key="preset_name",
                )
                if preset_name.startswith("Cytek"):
                    use_channels = st.toggle(
                        "Use the 64 detector channels",
                        value=True,
                        key="use_channels",
                    )
                else:
                    use_channels = False
                if LASER_PRESETS[preset_name] == "custom":
                    custom_txt = st.text_input(
                        "Laser lines, nm, comma-separated",
                        value="405, 488, 640",
                        key="custom_laser_txt",
                    )
        view = compute_view(dye_data, preset_name, use_channels, custom_txt)
        if view.get("error"):
            st.error(view["error"])
        else:
            theme = _theme()
            ink, muted, grid = theme["ink"], theme["muted"], theme["grid"]
            cosine = view["cosine"]
            matrix_key = (view["mode"], tuple(cosine.index))
            if st.session_state.get("matrix_key") != matrix_key:
                st.session_state.matrix_key = matrix_key
                st.session_state.selected_pair = sv.top_pair(cosine)
                st.session_state.pop("pair_table", None)
                st.session_state.pop("pair_token", None)
                st.session_state.pop("sim_heat_Cosine", None)
                st.session_state.pop("sim_heat_Pearson", None)
                for score_name in ("Cosine", "Pearson"):
                    st.session_state.pop(f"heat_token_{score_name}", None)

            with st.container(border=True):
                st.subheader(view["label"])
                with st.container(horizontal=True, vertical_alignment="center"):
                    score = st.segmented_control(
                        "Color the matrix by",
                        ["Cosine", "Pearson"],
                        default="Cosine",
                        key="score_metric",
                    )
                    show_rest = st.toggle("Show the rest of the panel", key="show_rest")
                if score not in ("Cosine", "Pearson"):
                    score = "Cosine"
                pair = st.session_state.get("selected_pair")
                names = set(cosine.index)
                if not pair or pair[0] not in names or pair[1] not in names:
                    pair = sv.top_pair(cosine)
                    st.session_state.selected_pair = pair
                if pair is None:
                    st.markdown("Only one dye has data. Add another dye. Overlap is a property of a pair.")
                else:
                    _draw_hero(view, pair, bool(show_rest), ink, muted, grid)
                    st.markdown(_hero_caption(view, pair))
                    st.caption(SCORE_NOTE)
                    if view["mode"] == "cytek" and view["extra"].get("em"):
                        st.markdown("**Emission shape**")
                        st.caption(
                            "The Aurora score above does not use this curve. "
                            "Solid is emission. Dashed is excitation. "
                            "Faint vertical lines mark the Aurora lasers."
                        )
                        em, ex = view["extra"]["em"], view["extra"]["ex"]
                        dyes = [name for name in ([*pair, *cosine.index] if show_rest else pair) if name in em]
                        # unique, pair first
                        seen = []
                        for name in dyes:
                            if name not in seen:
                                seen.append(name)
                        missing_curves = [name for name in pair if name not in em]
                        if missing_curves:
                            st.caption(
                                ", ".join(missing_curves)
                                + " has an Aurora signature and no wavelength curve, so it is not on this plot."
                            )
                        wave = sv.emission_chart(em, ex, seen, st.session_state.colors, ink=ink, show_lasers=True)
                        if wave is not None:
                            st.altair_chart(wave, theme=None)
                    for warning in view["warnings"]:
                        st.warning(warning)

            if pair is not None:
                scored = view["pearson"] if score == "Pearson" else view["cosine"]
                order = (
                    sv.order_by_peak(view["extra"]["signature"])
                    if view["mode"] == "cytek"
                    else list(scored.index)
                )
                # Peak order only includes dyes in the signature. Fall back if a name is missing.
                order = [name for name in order if name in scored.index]
                if len(order) != len(scored.index):
                    order = list(scored.index)
                signature = view["extra"].get("signature") if view["mode"] == "cytek" else None
                pairs = pair_frame(view["pearson"], view["cosine"], signature, pair, score)
                scheme, domain = ("blues", (0.0, 1.0)) if score == "Cosine" else ("redblue", (-1.0, 1.0))
                heat = sv.heatmap_chart(
                    scored,
                    order,
                    pair,
                    ink=ink,
                    scheme=scheme,
                    domain=domain,
                    diag_color=theme["diag"],
                )
                with st.container(border=True):
                    st.subheader("Pairs")
                    st.caption(SCALE_NOTE)
                    table_col, heat_col = st.columns([1, 1.15])
                    if st.session_state.pop("_reset_pair_table", False) or st.session_state.get("score_for_table") != score:
                        st.session_state.score_for_table = score
                        st.session_state.pop("pair_table", None)
                        st.session_state.pop("pair_token", None)
                    with table_col:
                        table_event = st.dataframe(
                            pairs,
                            hide_index=True,
                            height=460,
                            on_select="rerun",
                            selection_mode="single-row",
                            key="pair_table",
                            column_config={
                                "Cosine": st.column_config.NumberColumn(format="%.2f"),
                                "Pearson": st.column_config.NumberColumn(format="%.2f"),
                            },
                        )
                    with heat_col:
                        heat_event = st.altair_chart(
                            heat,
                            on_select="rerun",
                            key=f"sim_heat_{score}",
                            theme=None,
                        )
                    heat_pair = _cell_pair(getattr(heat_event, "selection", None))
                    table_rows = list(getattr(getattr(table_event, "selection", None), "rows", []) or [])
                    table_pair = None
                    if table_rows and table_rows[0] < len(pairs):
                        chosen = pairs.iloc[table_rows[0]]
                        table_pair = (chosen["Dye 1"], chosen["Dye 2"])
                    rerun = False
                    if _remember(repr(heat_pair), f"heat_token_{score}") and heat_pair:
                        if not sv.same_pair(st.session_state.get("selected_pair"), *heat_pair):
                            st.session_state.selected_pair = heat_pair
                            st.session_state._reset_pair_table = True
                            rerun = True
                    elif _remember(repr(table_pair), "pair_token") and table_pair:
                        if not sv.same_pair(st.session_state.get("selected_pair"), *table_pair):
                            st.session_state.selected_pair = table_pair
                            rerun = True
                    if rerun:
                        st.rerun()
                    _downloads(view, pairs)

missing = st.session_state.get("missing_dyes") or []
if missing and dye_data is not None and st.session_state.get("analyzed_queries"):
    st.warning("Not drawn: " + ", ".join(missing) + ". No Aurora signature.")
    for query in missing:
        with st.expander(f"Add a signature for {query}", icon=":material/upload:"):
            unresolved_panel(query)

if st.session_state.get("analyzed_queries"):
    with st.expander("Replace a spectrum for a dye already in the list", icon=":material/edit:"):
        st.caption(
            "What you add here takes precedence over the Cytek library and over FPbase. "
            "Choose Analyze panel again after it is saved."
        )
        target = st.selectbox(
            "Dye",
            st.session_state.analyzed_queries,
            key="manual_target",
        )
        if target:
            manual_source_ui(target, key_prefix="manual")

if dye_data and not st.session_state.get("analyzed_cytek_only", True):
    with st.expander("Wavelength names", icon=":material/manage_search:"):
        st.caption(
            "Confident FPbase matches are applied when you analyze. "
            "Correct one here if a wavelength curve is the wrong dye. "
            "The Aurora signature does not change."
        )
        lookup = st.session_state.get("owner_lookup") or {}
        name_rows = []
        for query in st.session_state.analyzed_queries:
            entry = dye_data.get(query) or {}
            name_rows.append(
                {
                    "Dye": query,
                    "FPbase": entry.get("fpbase_name") or st.session_state.selections.get(query) or "—",
                }
            )
        st.dataframe(pd.DataFrame(name_rows), hide_index=True)
        correct = st.selectbox("Correct one", st.session_state.analyzed_queries, key="fp_correct")
        options = fp.suggest_matches(correct, st.session_state.get("all_dye_names") or [], n=8)
        choice = st.selectbox("FPbase entry", options + [NOT_FOUND], key=f"fp_choice_{correct}")
        if st.button("Apply this match", key="apply_fp_match"):
            entry = dict(dye_data.get(correct) or {})
            if choice == NOT_FOUND or choice not in lookup:
                for key in ("EM", "EX", "AB"):
                    entry.pop(key, None)
                if entry.get("fpbase_name") and not str(entry.get("fpbase_name")).startswith("(Cytek"):
                    entry.pop("fpbase_name", None)
                st.session_state.selections[correct] = None
            else:
                entry.update(fp.fetch_dye_curves(choice, lookup[choice]))
                st.session_state.selections[correct] = choice
            if entry:
                dye_data[correct] = entry
            else:
                dye_data.pop(correct, None)
            st.session_state.dye_data = dye_data
            st.rerun()

st.header("Try a swap", icon=":material/swap_horiz:")
st.warning(SWAP_WARNING, icon=":material/warning:")
if panel_sig is None or len(panel_sig) < 2:
    st.caption("Analyze a panel with at least two Aurora signatures to score replacements.")
else:
    base = rec.panel_metrics(panel_sig)
    st.caption(
        "Suggestions use cosine of the Aurora 5L signatures. "
        "A replacement can lower the single closest pair and still spread overlap across more of the panel."
    )
    with st.container(horizontal=True):
        st.metric(
            "Closest pair",
            f"{base['max']:.3f}",
            help=" / ".join(base["worst_pair"]) if base["worst_pair"] else "Highest cosine in the panel.",
            border=True,
        )
        st.metric("Pairs above 0.5", base["n_over_0.5"], border=True)
        st.metric("Pairs above 0.4", base["n_over_0.4"], border=True)
    slots = rec.worst_partner_per_slot(panel_sig)
    st.caption("Closest partner for each dye.")
    st.dataframe(
        slots.rename(columns={
            "dye": "Dye",
            "worst_similarity": "Cosine",
            "worst_partner": "Closest partner",
            "peak_channel": "Peak",
        }),
        hide_index=True,
        column_config={"Cosine": st.column_config.NumberColumn(format="%.2f")},
    )
    slot = st.selectbox(
        "Which dye would you consider replacing?",
        list(slots["dye"]),
        key="swap_slot",
        help="The list is closest-first.",
    )
    if st.session_state.get("_viability_for") != slot:
        st.session_state._viability_for = slot
        st.session_state.viability_slot = (
            "zombie" in slot.lower() or "live" in slot.lower() or "viability" in slot.lower()
        )
    with st.container(horizontal=True):
        viability_slot = st.toggle(
            "This is the viability dye (no antibody attached)",
            key="viability_slot",
            help=(
                "Viability dyes can move anywhere in the spectrum, so the same-laser "
                "restriction is lifted and only viability reagents are proposed."
            ),
        )
        same_laser = st.toggle(
            "Restrict to the same laser",
            value=True,
            key="same_laser",
            disabled=viability_slot,
            help="Keeps the marker's brightness tier and reagent availability plausible.",
        )
    if st.button(
        "Find replacements (loads the reagent list once)",
        type="primary",
        icon=":material/find_replace:",
        key="find_swaps",
    ):
        with st.spinner("Loading the reagent list"):
            progress = st.progress(0.0)
            pool = fp.fetch_candidate_pool(
                cand_mod.ALL_CANDIDATES,
                progress_cb=lambda frac, name: progress.progress(min(frac, 1.0), text=f"Loading {name}"),
            )
            progress.empty()
        panel_names = {key: value.get("fpbase_name", key) for key, value in dye_data.items()}
        st.session_state.rec_result = (
            slot,
            rec.recommend_for_slot(
                slot,
                dye_data,
                pool,
                panel_names,
                same_laser_only=same_laser,
                viability_slot=viability_slot,
            ),
        )
        st.session_state.swap_pool_ready = True

    if "rec_result" in st.session_state:
        replaced, ranked = st.session_state.rec_result
        st.markdown(f"**Candidates to replace {replaced}**")
        if ranked.empty:
            st.info("No candidate in the curated reagent pool fits those constraints. Try turning off the same-laser restriction.")
        else:
            show = ranked.rename(columns={
                "slot_worst_after": "Closest cosine after",
                "slot_improvement": "Improvement",
                "new_worst_partner": "New closest partner",
                "panel_max_after": "Panel closest after",
                "panel_pairs_over_0.5": "Panel pairs above 0.5",
                "panel_pairs_over_0.4": "Panel pairs above 0.4",
                "peak_channel": "Peak",
                "laser": "Laser",
                "candidate": "Candidate",
            })
            if "Laser" in show.columns:
                show["Laser"] = show["Laser"].map(lambda value: sv.LASER_NM.get(value, value))
            st.dataframe(
                show,
                hide_index=True,
                height=380,
                column_config={
                    "Closest cosine after": st.column_config.NumberColumn(format="%.3f"),
                    "Improvement": st.column_config.NumberColumn(format="%.3f"),
                    "Panel closest after": st.column_config.NumberColumn(format="%.3f"),
                    "slot_worst_before": None,
                },
            )
            st.caption(
                f"Current panel: closest pair {base['max']:.3f}, "
                f"{base['n_over_0.5']} pairs above 0.5, {base['n_over_0.4']} pairs above 0.4. "
                "Prefer a candidate that lowers both the closest pair and the pair counts."
            )
            st.download_button(
                "Download candidate ranking",
                ranked.to_csv(index=False),
                f"swap_candidates_{replaced.replace(' ', '_')}.csv",
                "text/csv",
                key="dl_swaps",
            )
            pick = st.selectbox(
                f"Replace {replaced} with",
                list(ranked["candidate"]),
                key="swap_preview_pick",
            )
            pool = fp.fetch_candidate_pool(cand_mod.ALL_CANDIDATES)
            after_entries = rec.apply_swap(dye_data, replaced, pick, pool)
            sig_after = rec.cytek_signature(after_entries)
            after = rec.panel_metrics(sig_after)
            with st.container(horizontal=True):
                st.metric(
                    "Closest pair",
                    f"{after['max']:.3f}",
                    delta=f"{after['max'] - base['max']:+.3f}",
                    delta_color="inverse",
                    border=True,
                    help=" / ".join(after["worst_pair"]) if after["worst_pair"] else None,
                )
                st.metric(
                    "Pairs above 0.5",
                    after["n_over_0.5"],
                    delta=after["n_over_0.5"] - base["n_over_0.5"],
                    delta_color="inverse",
                    border=True,
                )
                st.metric(
                    "Pairs above 0.4",
                    after["n_over_0.4"],
                    delta=after["n_over_0.4"] - base["n_over_0.4"],
                    delta_color="inverse",
                    border=True,
                )
            d_max = after["max"] - base["max"]
            d_04 = after["n_over_0.4"] - base["n_over_0.4"]
            d_05 = after["n_over_0.5"] - base["n_over_0.5"]
            if d_max < -1e-9 and (d_04 > 0 or d_05 > 0):
                st.warning(
                    f"**Mixed result. Read past the headline number.** This swap lowers "
                    f"the single closest pair ({d_max:+.3f}) but increases the number of "
                    f"moderately similar pairs (above 0.4: {d_04:+d}, above 0.5: {d_05:+d}). "
                    "The replacement is spreading its overlap across more of the panel "
                    "instead of concentrating it in one pair. A swap like this usually "
                    "makes unmixing harder overall, not easier."
                )
            elif d_max >= -1e-9 and d_04 >= 0 and d_05 >= 0:
                st.info(
                    "This swap does not improve the panel. The closest pair lies elsewhere, "
                    "so changing this slot cannot move the headline number. Try the dye named "
                    "in the closest-pair metric above."
                )
            partner = slots.loc[slots["dye"] == replaced, "worst_partner"]
            partner_name = str(partner.iloc[0]) if len(partner) else ""
            new_partner = str(ranked.loc[ranked["candidate"] == pick, "new_worst_partner"].iloc[0])
            _swap_overlay(panel_sig, replaced, pick, partner_name, new_partner, pool)
            with st.expander("Matrix before and after"):
                order_before = sv.order_by_peak(panel_sig)
                order_after = [pick if name == replaced else name for name in order_before]
                cos_before = rec.cosine_matrix(panel_sig)
                cos_after = rec.cosine_matrix(sig_after)
                theme = _theme()
                before_chart = sv.heatmap_chart(
                    cos_before, order_before, None, selectable=False,
                    ink=theme["ink"], diag_color=theme["diag"],
                )
                after_chart = sv.heatmap_chart(
                    cos_after,
                    [name for name in order_after if name in cos_after.index],
                    None,
                    selectable=False,
                    ink=theme["ink"],
                    diag_color=theme["diag"],
                )
                before_col, after_col = st.columns(2)
                with before_col:
                    st.caption(f"Before, closest {base['max']:.3f}")
                    st.altair_chart(before_chart, theme=None)
                with after_col:
                    st.caption(f"After, {replaced} to {pick}, closest {after['max']:.3f}")
                    st.altair_chart(after_chart, theme=None)
                st.download_button(
                    "Download post-swap matrix",
                    cos_after.to_csv(),
                    f"cosine_after_swap_{pick.replace(' ', '_')}.csv",
                    "text/csv",
                    key="dl_after",
                )
