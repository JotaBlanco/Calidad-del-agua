"""
Step 1: Parameter name and unit normalization.

Maps scraped parameter names → BOE canonical names, identifies pesticides
and summation components, and normalizes unit strings.
"""

from __future__ import annotations

import pandas as pd


# ===================================================================
# Parameter name normalization
# ===================================================================

# Maps scraped parameter names → BOE canonical names.
# Only entries where the scraped name differs from the BOE name.
#
# Discrepancy categories:
#   - FMT:  formatting (capitalization, spacing, accents, punctuation)
#   - CAS:  CAS number appended in scraped data
#   - ABBR: abbreviation expanded / notation change
#   - QUAL: qualifier added or reworded
PARAM_RENAME_MAP: dict[str, str] = {
    # --- FMT: capitalization / spacing / accents / punctuation ----------
    "PH":                                    "pH",
    "Recuento de colonias a 22ºC":           "Recuento de colonias a 22 ºC",
    "Legionella spp":                        "Legionella spp.",
    "Colifagos somáticos":                   "Colífagos somáticos",
    "Indice de Langelier":                   "Índice de Langelier",
    "Radon":                                 "Radón",
    "Microcistina LR":                       "Microcistina-LR",

    # --- CAS: CAS number appended in scraped data ----------------------
    "Acrilamida (CAS 79-06-01)":             "Acrilamida",
    "Benceno (CAS 71-43-2)":                 "Benceno",
    "Benzo(a)pireno (CAS 50-32-8)":          "Benzo(a)pireno",
    "Bisfenol a (CAS 80-05-7)":              "Bisfenol A",
    "Cloruro de Vinilo (CAS 75-01-4)":       "Cloruro de vinilo",
    "Epiclorhidrina (CAS 106-89-8)":         "Epiclorhidrina",
    "1,2-Dicloroetano (CAS 107-06-2)":       "1-2-Dicloroetano",

    # --- ABBR: abbreviation / notation changes --------------------------
    "Actividad a total":                     "Actividad alfa total",
    "Actividad b resto":                     "Actividad beta resto",
    "Dosis Indicativa (Suma radionucleidos) DI": "Dosis Indicativa (DI)",
    "Suma 2 Tricloroeteno + Tetracloroeteno": "∑2 Tricloroeteno + Tetracloroeteno",
    "Suma 20 PFAs":                          "∑20 PFAS",
    "Suma 4 Hidrocarburos Policíclicos Aromáticos (HPA)": "∑4 Hidrocarburos Policíclicos Aromáticos (HPA)",
    "Suma 4 Trihalometanos (THM)":           "∑4 Trihalometanos (THM)",
    "Suma 5 AHAs":                           "∑5 Ácidos Haloacéticos (HAH)",
    "Suma total Plaguicidas":                "∑n Plaguicidas totales",

    # --- QUAL: "R: " prefix on natural radionuclides -------------------
    "R: Pb 210":                             "Pb 210",
    "R: Po 210":                             "Po 210",
    "R: Ra 226":                             "Ra 226",
    "R: Ra 228":                             "Ra 228",
    "R: U 234":                              "U 234",
    "R: U 238":                              "U 238",

    # --- QUAL: qualifier differences ------------------------------------
    "Enterococo":                            "Enterococo intestinal",
    "Dureza Total (CaCO3)":                  "Dureza total",
}


# ===================================================================
# Pesticide prefixes
# ===================================================================

# Prefixes identifying individual pesticides in the scraped data.
# All map to BOE "Plaguicida individual" limit (0.10 µg/L) and
# contribute collectively to "∑n Plaguicidas totales" (0.50 µg/L).
PESTICIDE_PREFIXES: tuple[str, ...] = (
    "PLA: A_",
    "PLA: NA_",
    "PLA: ",     # catch-all for other PLA variants (e.g. "PLA: Acrinatrin")
    "ISO: ",
    "MET: ",
)


# ===================================================================
# Summation component mapping
# ===================================================================

# Maps individual component (data name) → parent sum parameter (BOE name).
# These components have no individual BOE limit; only their sum is regulated.
COMPONENT_TO_SUM_MAP: dict[str, str] = {
    # --- THM → ∑4 Trihalometanos (THM) — limit: 100 µg/L ---------------
    "Cloroformo CAS 67-66-3":              "∑4 Trihalometanos (THM)",
    "Bromodiclorometano CAS 75-27-4":      "∑4 Trihalometanos (THM)",
    "Dibromoclorometano CAS 124-48-1":      "∑4 Trihalometanos (THM)",
    "Bromoformo CAS 75-25-2":              "∑4 Trihalometanos (THM)",

    # --- HAA → ∑5 Ácidos Haloacéticos (HAH) — limit: 60 µg/L ----------
    "Ácido dicloroacético CAS 79-43-6":     "∑5 Ácidos Haloacéticos (HAH)",
    "Ácido tricloroacético CAS 76-03-9":    "∑5 Ácidos Haloacéticos (HAH)",
    "Ácido monocloroacético CAS 79-11-8":   "∑5 Ácidos Haloacéticos (HAH)",
    "Ácido dibromoacético CAS 631-64-1":    "∑5 Ácidos Haloacéticos (HAH)",
    "Ácido monobromoacético CAS 79-08-3":   "∑5 Ácidos Haloacéticos (HAH)",

    # --- HPA → ∑4 Hidrocarburos Policíclicos Aromáticos (HPA) — 0.10 µg/L
    "Benzo(b)fluoranteno CAS 205-99-2":     "∑4 Hidrocarburos Policíclicos Aromáticos (HPA)",
    "Benzo(ghi)perileno CAS 191-24-2":      "∑4 Hidrocarburos Policíclicos Aromáticos (HPA)",
    "Benzo(k)fluoranteno CAS 207-08-9":     "∑4 Hidrocarburos Policíclicos Aromáticos (HPA)",
    "Indeno(1,2,3-cd)pireno CAS 193-39-5":  "∑4 Hidrocarburos Policíclicos Aromáticos (HPA)",

    # --- ∑2 Tricloroeteno + Tetracloroeteno — limit: 10 µg/L -----------
    "Tricloroeteno CAS 79-01-6":            "∑2 Tricloroeteno + Tetracloroeteno",
    "Tetracloroeteno CAS 127-18-4":         "∑2 Tricloroeteno + Tetracloroeteno",

    # --- PFAS → ∑20 PFAS — limit: 0.10 µg/L ---------------------------
    "Acido perfluorodecano sulfónico (PFDS) CAS: 335-77-3":  "∑20 PFAS",
    "Ácido perfluorobutanoico (PFBA) CAS: 375-22-4":         "∑20 PFAS",
    "Ácido perfluorobutanosulfónico (PFBS) CAS: 375-73-5":   "∑20 PFAS",
    "Ácido perfluorodecanoico (PFDA) CAS: 335-76-2":         "∑20 PFAS",
    "Ácido perfluorododecano sulfónico (PFDoS) CAS: 79780-39-5": "∑20 PFAS",
    "Ácido perfluorododecanoico (PFDoDA) CAS: 307-55-1":     "∑20 PFAS",
    "Ácido perfluoroheptano sulfónico (PFHpS) CAS: 375-92-8": "∑20 PFAS",
    "Ácido perfluoroheptanoico (PFHpA) CAS: 375-85-9":       "∑20 PFAS",
    "Ácido perfluorohexanoico (PFHxA) CAS: 307-24-4":        "∑20 PFAS",
    "Ácido perfluorohexanosulfónico (PFHxS) CAS: 355-46-4":  "∑20 PFAS",
    "Ácido perfluorononanoico PFNA CAS 375-95-1":             "∑20 PFAS",
    "Ácido perfluorononanosulfónico (PFNS) CAS: 68259-12-1": "∑20 PFAS",
    "Ácido perfluorooctanoico PFOA CAS 335-67-1":             "∑20 PFAS",
    "Ácido perfluorooctanosulfónico PFOS CAS 1763-23-1":      "∑20 PFAS",
    "Ácido perfluoropentanoico (PFPeA) CAS: 2706-90-3":      "∑20 PFAS",
    "Ácido perfluoropentanosulfónico (PFPeS) CAS: 2706-91-4": "∑20 PFAS",
    "Ácido perfluorotridecano sulfónico (PFTris) CAS: -":    "∑20 PFAS",
    "Ácido perfluorotridecanoico (PFTrDA) CAS: 72629-94-8":  "∑20 PFAS",
    "Ácido perfluoroundecano sulfónico (PFUnS) CAS: 749786-16-1": "∑20 PFAS",
    "Ácido perfluoroundecanoico (PFUnDA) CAS: 2058-94-8":    "∑20 PFAS",
}


# ===================================================================
# Unit normalization
# ===================================================================

# Unit name normalization: data unit → BOE canonical unit.
# All mismatches are naming-only (no numeric conversion needed).
UNIT_RENAME_MAP: dict[str, str] = {
    # Microbiological — different formatting, same count
    "NMP/100ml":        "UFC o NMP / 100 ml",
    "UFC/100 ml":       "UFC / 100 ml",
    "UFP/100 ml":       "UFP / 100 ml",
    "UFC/L":            "UFC en 1 L",
    "UFC/1 ml":         "UFC / 1 ml",
    # Physical / chemical
    "mg Pt-Co/L":       "mg/L Pt/Co",
    "µS/cm a 20ºC":     "µS/cm a 20 ºC",
    "mg O2 /L":         "mg/L O2",
    "mSv/año":          "mSv",
    "In. Dil.":         "Índice dilución",
    "Unidades pH":      "Unidades pH",
}


# ===================================================================
# Functions
# ===================================================================

def normalize_parametro(name: str) -> str:
    """Normalize a scraped parameter name to its BOE canonical form.

    Returns the original name unchanged if no mapping exists.
    """
    return PARAM_RENAME_MAP.get(name, name)


def normalize_unidad(unit: str) -> str:
    """Normalize a data unit string to its BOE canonical form."""
    if pd.isna(unit):
        return unit
    return UNIT_RENAME_MAP.get(unit, unit)


def is_pesticide(name: str) -> bool:
    """Check if a parameter name represents an individual pesticide."""
    return any(name.startswith(p) for p in PESTICIDE_PREFIXES)
