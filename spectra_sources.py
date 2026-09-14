"""Where to find a spectrum that FPbase does not have.

Deliberately links rather than scrapes. Every vendor spectra viewer worth using
(BioLegend, BD, Thermo, Cytek, AAT Bioquest) is a client-rendered app that pulls its
curves from an undocumented private endpoint after user interaction -- nothing is in the
served HTML. Those endpoints can be reverse-engineered, but they are unversioned, break
without notice, and automated access generally sits outside the sites' terms of use.

So the tool does the part it can do reliably: take you straight to the right page for the
dye you named. Screenshot the plot there and feed it to the digitisers, which is a normal
read of a published figure.

Ordered best-first for spectral flow: sources that publish a Cytek/Aurora *channel
signature* come first, because that is the instrument's own measured signature and beats
any wavelength spectrum for this analysis.
"""
from __future__ import annotations

import urllib.parse

SOURCES = [
    {
        "name": "BioLegend — Fluorescence Spectra Analyzer",
        "url": "https://www.biolegend.com/en-us/spectra-analyzer?fluors={q}",
        "gives": "signature + spectrum",
        "note": "Publishes Cytek channel signatures for its own dyes (Spark, Fire, "
                "Zombie, KIRAVIA). Best first stop for BioLegend reagents.",
    },
    {
        "name": "Cytek — Full Spectrum Viewer",
        "url": "https://spectrum.cytekbio.com/?search={q}",
        "gives": "signature",
        "note": "Signatures for the exact Aurora configuration you run — pick "
                "5L 16UV-16V-14B-10YG-8R.",
    },
    {
        "name": "BD — Spectrum Viewer",
        "url": "https://www.bdbiosciences.com/en-us/resources/bd-spectrum-viewer?search={q}",
        "gives": "signature + spectrum",
        "note": "The place to look for BUV / BV / BB dyes, including BUV 615.",
    },
    {
        "name": "Thermo Fisher — Fluorescence SpectraViewer",
        "url": "https://www.thermofisher.com/order/fluorescence-spectraviewer#!/search/{q}",
        "gives": "spectrum",
        "note": "Alexa Fluor, eFluor, Super Bright, LIVE/DEAD, Qdot.",
    },
    {
        "name": "AAT Bioquest — Fluorescence Spectrum Viewer",
        "url": "https://www.aatbio.com/fluorescence-excitation-emission-spectrum-graph-viewer?search={q}",
        "gives": "spectrum",
        "note": "Broad third-party coverage; useful when the vendor page is thin.",
    },
    {
        "name": "FPbase spectra search (whole site)",
        "url": "https://www.fpbase.org/spectra/?q={q}",
        "gives": "numeric",
        "note": "Worth a look even when this app found nothing — the name may differ "
                "from what you typed. If you find it here, tell me the exact name and "
                "it will import numerically, no screenshot needed.",
    },
    {
        "name": "Google — vendor spectrum page",
        "url": "https://www.google.com/search?q={q}+fluorophore+spectrum+excitation+emission",
        "gives": "search",
        "note": "Fallback for discontinued or newly released reagents.",
    },
]


def links_for(query: str) -> list[dict]:
    """Search URLs for one fluorophore name, best-first."""
    q = urllib.parse.quote_plus(query.strip())
    return [{**s, "href": s["url"].format(q=q)} for s in SOURCES]
