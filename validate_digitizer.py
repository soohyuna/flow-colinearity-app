"""Measure digitiser accuracy against known ground truth.

Renders a real FPbase spectrum as a vendor-spectra-viewer-style plot (shaded area under
a solid emission curve, a dashed excitation curve, gridlines, legend), digitises it back
out, and compares to the source data. Run it after touching spectra_image.py.

    python3 validate_digitizer.py
"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import spectra_image as si
import fpbase_client as fp
import recommend as rec

DYE = "APC/Fire-750"          # closest public analogue to APC/Fire 810
EM_HEX, EX_HEX = "#d62728", "#1f77b4"
WL_LO, WL_HI = 350, 900
IMG = "/tmp/validate_spectra_viewer.png"


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def main():
    g = np.arange(WL_LO, WL_HI + 1, 1.0)
    curves = fp.fetch_dye_curves(DYE, fp.build_owner_lookup(fp.get_dye_index())[DYE])

    def norm(pts):
        a = np.array(pts, float)
        v = np.interp(g, a[:, 0], np.clip(a[:, 1], 0, None), left=0, right=0)
        return v / v.max()

    em_true = norm(curves["EM"])
    ex_true = norm(curves["EX"] if "EX" in curves else curves["AB"])

    fig, ax = plt.subplots(figsize=(10, 5), dpi=110)
    ax.fill_between(g, 0, em_true * 100, color=EM_HEX, alpha=0.35, lw=0)
    ax.plot(g, em_true * 100, color=EM_HEX, lw=2.0, label=f"{DYE} Emission")
    ax.plot(g, ex_true * 100, color=EX_HEX, lw=2.0, ls="--", label=f"{DYE} Excitation")
    ax.set_xlim(WL_LO, WL_HI); ax.set_ylim(0, 100)
    ax.grid(alpha=0.35); ax.legend(loc="upper right")
    ax.set_xlabel("Wavelength (nm)"); ax.set_ylabel("Normalized intensity (%)")
    plt.tight_layout()
    plt.savefig(IMG)
    fig.canvas.draw()
    bb = ax.get_window_extent()
    H = fig.canvas.get_width_height()[1]
    box = (bb.x0, H - bb.y1, bb.x1, H - bb.y0)
    plt.close(fig)

    rgb = si.load_rgb(IMG)
    x0, y0, x1, y1 = box
    xc, yc = ((x0, WL_LO), (x1, WL_HI)), ((y1, 0.0), (y0, 100.0))

    # legend swatch boxes, located once by inspecting the rendered figure
    EM_SWATCH = [(828, 36, 872, 50)]
    EX_SWATCH = [(830, 60, 868, 72)]

    print(f"image {rgb.shape[1]}x{rgb.shape[0]}  plot box {tuple(round(v, 1) for v in box)}\n")
    rows = []
    for label, hexcol, excl, truth in [
        ("emission (solid+filled)", EM_HEX, EM_SWATCH, em_true),
        ("excitation (dashed)", EX_HEX, EX_SWATCH, ex_true),
    ]:
        got = si.extract_curve(rgb, box, hex2rgb(hexcol), xc, yc,
                               tolerance=60, exclude=excl, wl_grid=g)[:, 1]
        e = np.abs(got - truth)
        print(f"{label:<26} mean {e.mean():.4f}  max {e.max():.4f}  "
              f"RMSE {np.sqrt((e**2).mean()):.4f}  "
              f"peak {g[np.argmax(got)]:.0f} nm (true {g[np.argmax(truth)]:.0f})")
        rows.append(got)

    print("\nlegend-artifact detector:")
    for label, hexcol, excl, want in [
        ("emission,   legend present", EM_HEX, None, "WARN"),
        ("emission,   legend excluded", EM_HEX, EM_SWATCH, "clean"),
        ("excitation, legend present", EX_HEX, None, "WARN"),
        ("excitation, legend excluded", EX_HEX, EX_SWATCH, "clean"),
    ]:
        cur = si.extract_curve(rgb, box, hex2rgb(hexcol), xc, yc,
                               tolerance=60, exclude=excl, wl_grid=g)
        f = si.find_flat_runs(cur)
        got = "WARN" if f else "clean"
        print(f"   {label:<30} {got:<6} {'OK' if got == want else '<<< MISMATCH'}")

    # what the error does to the numbers the app actually reports
    import json
    panel_path = ("/private/tmp/claude-501/-Users-sooahn-Downloads-CQ1/"
                  "031599c7-fbbb-44d7-8e57-4ec0cee56532/scratchpad/dye_spectra.json")
    try:
        panel = json.load(open(panel_path))["data"]
    except OSError:
        print("\n(skipping downstream check -- reference panel not present)")
        return

    def sig(em):
        return rec.cytek_signature({**panel, DYE: {
            "EM": np.column_stack([g, em]).tolist(),
            "EX": np.column_stack([g, ex_true]).tolist()}})

    st_, sd_ = sig(em_true), sig(rows[0])
    d = (rec.cosine_matrix(sd_)[DYE].drop(DYE) - rec.cosine_matrix(st_)[DYE].drop(DYE)).abs()
    print(f"\ndownstream effect on reported colinearity:"
          f"\n   max change {d.max():.4f}, mean change {d.mean():.4f}"
          f"\n   peak channel: true {st_.loc[DYE].idxmax()} / digitised {sd_.loc[DYE].idxmax()}")


if __name__ == "__main__":
    main()
