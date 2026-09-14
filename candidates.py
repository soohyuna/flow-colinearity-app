"""Curated pool of real, commercially-available flow-cytometry reagents that exist on
FPbase, used as replacement candidates when recommending panel improvements.

Curated rather than "every dye on FPbase" on purpose: a recommendation is only useful
if the reagent actually exists conjugated to the clone you need. Anything auto-mined
from the full database will happily suggest an obscure microscopy dye that no vendor
sells as an antibody conjugate.

`VIABILITY` dyes are tracked separately because they carry no antibody -- that slot can
move anywhere in the spectrum, which makes it the cheapest fix in almost any panel.
"""

VIABILITY = [
    "Zombie UV", "Zombie Violet", "Zombie Aqua", "Zombie Green", "Zombie Red",
    "Zombie Yellow", "Zombie NIR",
    "LIVE/DEAD Fixable Blue", "LIVE/DEAD Fixable Aqua", "LIVE/DEAD Fixable Green",
    "LIVE/DEAD Fixable Violet", "LIVE/DEAD Fixable Yellow", "LIVE/DEAD Fixable Red",
    "LIVE/DEAD Fixable Far Red", "LIVE/DEAD Fixable Near-IR",
    "Fixable Viability Dye eFluor 455UV", "Fixable Viability Dye eFluor 506",
    "Fixable Viability Dye eFluor 660", "Fixable Viability Dye eFluor 780",
]

ANTIBODY_CONJUGATES = [
    # UV 355
    "BD Horizon BUV395", "BD Horizon BUV496", "BD Horizon BUV563",
    "BD Horizon BUV661", "BD Horizon BUV737", "BD Horizon BUV805",
    # Violet 405
    "Brilliant Violet 421", "Brilliant Violet 480", "Brilliant Violet 510",
    "Brilliant Violet 570", "Brilliant Violet 605", "Brilliant Violet 650",
    "Brilliant Violet 711", "Brilliant Violet 750", "Brilliant Violet 785",
    "Super Bright 436", "Super Bright 600", "Super Bright 645", "Super Bright 702",
    "Super Bright 780", "Spark Violet 538", "BD Horizon V450", "BD Horizon V500",
    "eFluor 450", "eFluor 506", "Pacific Blue",
    # Blue 488
    "BD Horizon BB515", "Alexa Fluor 488", "Alexa Fluor 532", "Spark Blue 550",
    "PerCP", "PerCP-Cy5.5", "PerCP-eFluor 710", "BD Horizon - BB700",
    # Yellow-green 561
    "PE (R-PE / R-phycoerythrin)", "PE/Dazzle 594", "PE-CF594", "PE-Texas Red",
    "PE-eFluor 610", "PE-Cy5", "PE-Cy5.5", "PE-Cy7", "PE-Alexa Fluor 610",
    "PE-Alexa Fluor 680", "PE-Alexa Fluor 700", "PE/Fire 780",
    # Red 640
    "APC (allophycocyanin)", "Alexa Fluor 647", "Alexa Fluor 660", "Alexa Fluor 700",
    "APC/Cy7", "APC-eFluor 780", "APC/Fire-750", "APC/H7", "APC-Alexa Fluor 750",
]

ALL_CANDIDATES = VIABILITY + ANTIBODY_CONJUGATES

def is_viability(name: str) -> bool:
    return name in set(VIABILITY)
