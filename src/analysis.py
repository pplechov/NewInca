# -*- coding: utf-8 -*-
"""
analysis.py -- project -> quantitative results.

Combines the project reader (inca_ipj), the peak-integration/physics layer
(quant) and the separate calibration folder:

    calibration/xpp_standards.csv     the default profile's standards table,
                                      exported unchanged from XPP Standards.mdb
    calibration/profiles/<name>/      one standards table per instrument /
                                      analytical protocol, chosen by detector
                                      serial number (see quant.suggest_for)
    calibration/mac.csv               mass absorption coefficients
    calibration/elements.csv          atomic weights, fluorescence yields
    calibration/lines.csv             line energies

Nothing here is hard-wired to one particular database: add a profile (or point
NEWINCA_CALIB somewhere) and the same program analyses other instruments.
"""
import os, sys, csv, math, re

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import inca_ipj as I
import quant as Q

# element: Z, Ka energy keV, oxide, cations in oxide, oxygens, cation mass, oxide mass
#
# The list is deliberately wider than the classical "silicate ten": a chromite,
# a sulphide or a Ni-Cu ore shows Cr, V, Ni, S and Zn peaks in the spectrum, and
# a result that silently drops the elements it was told about is worse than an
# imprecise number.  An element without a conventional oxide (C, O, F, Cl) is
# reported as the element itself.
EL = {
    "C":  (6,  0.2774, None,     0, 0, 12.011, 12.011),
    "O":  (8,  0.5249, None,     0, 0, 15.999, 15.999),
    "F":  (9,  0.6768, None,     0, 0, 18.998, 18.998),
    "Na": (11, 1.0410, "Na2O",   2, 1, 22.990, 61.979),
    "Mg": (12, 1.2536, "MgO",    1, 1, 24.305, 40.304),
    "Al": (13, 1.4867, "Al2O3",  2, 3, 26.982, 101.961),
    "Si": (14, 1.7399, "SiO2",   1, 2, 28.086, 60.084),
    "P":  (15, 2.0102, "P2O5",   2, 5, 30.974, 141.945),
    "S":  (16, 2.3078, "SO3",    1, 3, 32.060, 80.060),
    "Cl": (17, 2.6224, None,     0, 0, 35.453, 35.453),
    "K":  (19, 3.3138, "K2O",    2, 1, 39.098, 94.196),
    "Ca": (20, 3.6917, "CaO",    1, 1, 40.078, 56.077),
    "Ti": (22, 4.5109, "TiO2",   1, 2, 47.867, 79.866),
    "V":  (23, 4.9522, "V2O3",   2, 3, 50.942, 149.881),
    "Cr": (24, 5.4147, "Cr2O3",  2, 3, 51.996, 151.990),
    "Mn": (25, 5.8988, "MnO",    1, 1, 54.938, 70.937),
    "Fe": (26, 6.4039, "FeO",    1, 1, 55.845, 71.844),
    "Co": (27, 6.9303, "CoO",    1, 1, 58.933, 74.932),
    "Ni": (28, 7.4781, "NiO",    1, 1, 58.693, 74.692),
    "Cu": (29, 8.0478, "CuO",    1, 1, 63.546, 79.545),
    "Zn": (30, 8.6389, "ZnO",    1, 1, 65.380, 81.379),
}

OXIDE_ORDER = ["SiO2", "TiO2", "Al2O3", "Cr2O3", "V2O3", "FeO", "MnO", "MgO",
               "CaO", "Na2O", "K2O", "P2O5", "SO3", "NiO", "CoO", "CuO", "ZnO"]

# K-beta energies of the same lines: measured only to take the K-beta of one
# element out of the window of the K-alpha of the next one (Cr Kb -> Mn Ka,
# Ti Kb -> V Ka, Fe Kb -> Co Ka ...).  From calibration\lines.csv.
KB = {
    "Al": 1.5541, "Si": 1.8359, "P": 2.1356, "S": 2.4640, "Cl": 2.8156,
    "K": 3.5896, "Ca": 4.0127, "Ti": 4.9318, "V": 5.4273, "Cr": 5.9467,
    "Mn": 6.4904, "Fe": 7.0580, "Co": 7.6494, "Ni": 8.2647, "Cu": 8.9053,
    "Zn": 9.5720,
}

# K-beta / K-alpha intensity ratio, used only to know how much of a K-beta to
# subtract from the window of the next element's K-alpha.  Not critical: the
# subtracted part is a tenth of a peak, so an error of 20 % here is 2 % there.
KB_RATIO = {
    "Al": 0.045, "Si": 0.050, "P": 0.090, "S": 0.100, "Cl": 0.110,
    "K": 0.120, "Ca": 0.120, "Ti": 0.135, "V": 0.137, "Cr": 0.138,
    "Mn": 0.135, "Fe": 0.133, "Co": 0.132, "Ni": 0.130, "Cu": 0.128,
    "Zn": 0.126,
}

# symbols for reporting the element lists of the projects (not all are quantified)
_Z_SYMBOL = dict(enumerate(
    "- H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co "
    "Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te "
    "I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir "
    "Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu".split(), 0))

# The elements used to decide which of the detectors of a table is "the" one:
# the classical quantitative set.  Deliberately fixed, so that extending EL does
# not silently switch the standards of an existing calibration.
COVER_SET = ("O", "Na", "Mg", "Al", "Si", "K", "Ca", "Ti", "Mn", "Fe")

# compositions of the standards, used only for the absorption correction; the
# matrix matching of these standards to the sample makes it a small term
STD_COMP = {
    "SiO2":                  {8: 0.5326, 14: 0.4674},
    "jadeite":               {8: 0.5162, 11: 0.0830, 13: 0.0974, 14: 0.3034},
    "olivine Spr USNM 2566": {8: 0.4390, 12: 0.2790, 14: 0.2820},
    "Kyanite":               {8: 0.4940, 13: 0.3330, 14: 0.1730},
    "Microclin":             {8: 0.4592, 19: 0.1405, 13: 0.0976, 14: 0.3027},
    "diopside USNM 117733":  {8: 0.4460, 20: 0.1850, 12: 0.1090, 14: 0.2600},
    "SrTiO3":                {8: 0.1610, 38: 0.5880, 22: 0.3210},
    "Mn2SiO4":               {8: 0.3250, 25: 0.5380, 14: 0.1370},
    "FeS2":                  {16: 0.5345, 26: 0.4655},
}

# Mineral names, spelled out to formulas.  Standards are labelled by the
# operator ("Di N1", "Kfs-143966", "Hbl-111356"), and INCA itself knows the full
# composition of every standard it was set up with -- that composition is what
# the absorption correction needs on the standard side.  The files INCA ships
# hold only "Conc Std" of the analysed element, so the matrix is recovered here:
# from the chemical formula when the label is one, from this table when it is a
# mineral name.  An unknown label means "no correction", exactly as before.
MINERALS = {
    "di": "CaMgSi2O6", "diopside": "CaMgSi2O6", "cpx": "CaMgSi2O6",
    "aug": "CaMgSi2O6", "augite": "CaMgSi2O6",
    "kfs": "KAlSi3O8", "kfeldspar": "KAlSi3O8", "ort": "KAlSi3O8",
    "microclin": "KAlSi3O8", "orthoclase": "KAlSi3O8", "sanidine": "KAlSi3O8",
    "ab": "NaAlSi3O8", "albite": "NaAlSi3O8",
    "an": "CaAl2Si2O8", "anorthite": "CaAl2Si2O8",
    "ol": "Mg1.8Fe0.2SiO4", "olivine": "Mg2SiO4", "fo": "Mg2SiO4",
    "fa": "Fe2SiO4", "fayalite": "Fe2SiO4",
    "hbl": "NaCa2Mg4Al3Si6O22O2H2", "amp": "NaCa2Mg4Al3Si6O22O2H2",
    "hornblende": "NaCa2Mg4Al3Si6O22O2H2",
    "grt": "Fe3Al2Si3O12", "garnet": "Fe3Al2Si3O12", "alm": "Fe3Al2Si3O12",
    "bt": "KMg3AlSi3O10O2H2", "biotite": "KMg3AlSi3O10O2H2",
    "ms": "KAl3Si3O10O2H2", "muscovite": "KAl3Si3O10O2H2",
    "qz": "SiO2", "qtz": "SiO2", "quartz": "SiO2",
    "woll": "CaSiO3", "wollastonite": "CaSiO3",
    "ap": "Ca5P3O13H", "apatite": "Ca5P3O13H",
    "crt": "FeCr2O4", "chromite": "FeCr2O4",
    "ilm": "FeTiO3", "ilmenite": "FeTiO3",
    "sp": "MgAl2O4", "spinel": "MgAl2O4",
    "sph": "CaTiSiO5", "titanite": "CaTiSiO5",
    "pl": "Na0.5Ca0.5Al1.5Si2.5O8", "plag": "Na0.5Ca0.5Al1.5Si2.5O8",
    "plagioclase": "Na0.5Ca0.5Al1.5Si2.5O8",
    "py": "FeS2", "pyr": "FeS2", "pyrite": "FeS2",
    "cc": "CaCO3", "calcite": "CaCO3",
    "dol": "CaMg(CO3)2", "dolomite": "CaMg2C2O6",
}

_TOKEN = re.compile(r"([A-Z][a-z]?)(\d*\.?\d*)")

# hydrogen is not quantified, but it is part of a mineral matrix (amphiboles,
# micas, apatite) and therefore of the absorption correction
_EXTRA = {"H": (1, 1.008)}


def _elem(sym):
    """(Z, atomic mass) for an element symbol, or None.

    EL holds the elements newINCA quantifies; a standard may still contain an
    element outside that list (Ga, As, Cs, Re in the Camebax table), so the full
    element table of the physics layer is consulted as well.
    """
    if sym in EL:
        return EL[sym][0], EL[sym][5]
    if sym in _EXTRA:
        return _EXTRA[sym]
    try:
        row = Q._load()["el"]
    except Exception:
        return None
    for z, r in row.items():
        if r.get("symbol") == sym:
            try:
                return int(z), float(r["atomic_weight"])
            except (TypeError, ValueError):
                return None
    return None


def _parse_formula(text):
    """'CaMgSi2O6' -> {'Ca': 1, 'Mg': 1, 'Si': 2, 'O': 6}; None when not a formula."""
    s = (text or "").strip()
    if not s or len(s) > 40:
        return None
    pos, out = 0, {}
    while pos < len(s):
        m = _TOKEN.match(s, pos)
        if not m:
            return None
        sym, num = m.group(1), m.group(2)
        if _elem(sym) is None:
            return None
        n = float(num) if num else 1.0
        if not (0.05 <= n <= 60):
            return None
        out[sym] = out.get(sym, 0.0) + n
        pos = m.end()
    return out or None


def _counts_to_weights(counts):
    """{'Si': 1, 'O': 2} -> {14: 0.4674, 8: 0.5326} (weight fractions)"""
    mass = {}
    total = 0.0
    for sym, n in counts.items():
        z_m = _elem(sym)
        if z_m is None:
            return None
        m = z_m[1] * n
        mass[z_m[0]] = mass.get(z_m[0], 0.0) + m
        total += m
    if total <= 0:
        return None
    return {z: m / total for z, m in mass.items()}


def standard_composition(label, conc=None, symbol=None):
    """Full composition of a standard, {Z: weight fraction}, or None.

    ``conc`` is the analysed element's concentration from the standards table and
    ``symbol`` its symbol: a formula that disagrees with it (a label such as
    "SiO295", where "95" is a measurement code, not an oxygen count) is rejected
    instead of being silently trusted.
    """
    name = (label or "").strip()
    if not name:
        return None
    if name in STD_COMP:
        return STD_COMP[name]
    tokens = [t for t in re.split(r"[\s\-_/:]+", name) if t]
    cands = [name]
    if tokens:
        cands.append(tokens[0])                   # "CaF2 A", "FeS2 B"
    cands.extend(tokens)
    for cand in cands:
        w = _counts_to_weights(_parse_formula(cand) or {})
        if w and _agrees(w, conc, symbol):
            return w
    # "Di N1", "Kfs-143966", "Hbl-111356", "Cpx Cr-Aug-164905"
    for token in tokens:
        key = token.strip(".").lower()
        if key in MINERALS:
            w = _counts_to_weights(_parse_formula(MINERALS[key]) or {})
            if w and _agrees(w, conc, symbol, tol=0.6):
                return w
    return None


def _agrees(weights, conc, symbol, tol=0.35):
    """Does the formula put roughly the measured concentration in the standard?"""
    if conc is None or symbol not in EL:
        return True
    if conc <= 0:
        return True
    got = weights.get(EL[symbol][0], 0.0)
    if got <= 0:
        return False
    return abs(got - conc) <= tol * max(conc, 1e-6)


def _as_int(v, default=None):
    try:
        return int(float(str(v).strip()))
    except Exception:
        return default


def _as_float(v, default=None):
    try:
        return float(str(v).strip())
    except Exception:
        return default


def load_standards(cal_dir=None, detector=None, e0=None, prefer_newest=False):
    """Read the XPP standards table and choose one row per element.

    cal_dir  : calibration profile folder (default: the active profile)
    detector : preferred ``Detector Type``; None = the type that covers the most
               elements of interest (1 in every table seen so far)
    e0       : accelerating voltage in kV; None = 20 kV if the table has it,
               otherwise the most common voltage in the table

    Tables from different instruments have the same 16 columns (they are all
    exports of INCA's "Standards data" table), so the only thing that changes
    between a Camebax table and an X-Max table is the numbers.
    """
    path = (os.path.join(cal_dir, "xpp_standards.csv") if cal_dir
            else Q.standards_csv())
    if not os.path.exists(path):
        raise FileNotFoundError("нет калибровочного файла: %s" % path)
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig"), delimiter=";"))
    clean = []
    for r in rows:
        if _as_int(r.get("Atomic Number")) is None or _as_int(r.get("Line Type")) is None:
            continue
        if _as_float(r.get("Norm Area Std")) is None:
            continue
        clean.append(r)

    voltages = [v for v in (_as_int(r.get("E0")) for r in clean) if v is not None]
    if e0 is None:
        e0 = 20 if 20 in voltages else (
            max(set(voltages), key=voltages.count) if voltages else 20)

    def covers(det):
        # A fixed set, not the whole of EL: the extended element list must not
        # change which detector (and therefore which standard rows) is chosen
        # for a table that carries measurements on two detectors.
        n = 0
        for sym in COVER_SET:
            z = EL[sym][0]
            if any(_as_int(r["Atomic Number"]) == z
                   and _as_int(r["Line Type"]) == 1000
                   and _as_int(r["Detector Type"]) == det for r in clean):
                n += 1
        return n

    detectors = [d for d in (_as_int(r.get("Detector Type")) for r in clean)
                 if d is not None]
    if detector is None and detectors:
        detector = max(set(detectors), key=covers)

    out = {}
    for sym, (z, e, *_r) in EL.items():
        def cands_at(voltage):
            return [r for r in clean
                    if _as_int(r["Atomic Number"]) == z
                    and _as_int(r["Line Type"]) == 1000
                    and _as_int(r["E0"]) == voltage
                    and (r.get("Label") or "").strip() not in ("Not defined", "")]

        cands = cands_at(e0)
        if not cands:
            # a table measured at a different kV: still better than nothing
            cands = [r for r in clean
                     if _as_int(r["Atomic Number"]) == z
                     and _as_int(r["Line Type"]) == 1000
                     and (r.get("Label") or "").strip() not in ("Not defined", "")]
        if not cands:
            continue
        # prefer the requested detector, then the oldest (or newest) measurement
        off_det = [r for r in cands if _as_int(r["Detector Type"]) != detector]
        if off_det and len(off_det) < len(cands):
            cands = [r for r in cands if _as_int(r["Detector Type"]) == detector]
        cands.sort(key=lambda r: r.get("Date") or "", reverse=bool(prefer_newest))
        r = cands[0]
        conc = (_as_float(r.get("Conc Std"), 0.0) or 0.0) / 100.0
        label = (r.get("Label") or "").strip()
        out[sym] = {"z": z, "e": e,
                    "conc": conc,
                    "area": _as_float(r.get("Norm Area Std"), 0.0) or 0.0,
                    "label": label,
                    "comp": standard_composition(label, conc, sym),
                    "date": r.get("Date") or "",
                    "det": _as_int(r.get("Detector Type"), 0)}
    return out


def _comp_by_z(comp):
    """{symbol: weight fraction} -> {Z: weight fraction} for the μ/ρ mixture.

    A composition that is already keyed by atomic number (STD_COMP and anything
    built by standard_composition) is passed through unchanged.
    """
    out = {}
    for key, w in (comp or {}).items():
        if w <= 0:
            continue
        if isinstance(key, int):
            out[key] = out.get(key, 0.0) + w
            continue
        if key not in EL:
            continue
        z = EL[key][0]
        out[z] = out.get(z, 0.0) + w
    return out


def absorption_factor(comp, e_keV, e0=20.0, take_off=40.0):
    """Philibert absorption factor f(chi) for a composition, 0 < f <= 1.

    ``comp`` is {symbol: weight fraction}; μ/ρ of the mixture comes from the
    selected absorption table (see quant.mac_file), so switching the table in the
    GUI switches the correction.  E0 in kV, take-off angle in degrees.
    """
    mu = Q.mac_mixture(_comp_by_z(comp), e_keV)
    if mu <= 0:
        return 1.0
    chi = mu / math.sin(math.radians(take_off))
    sig = 2.39e5 / max(e0 ** 1.5 - e_keV ** 1.5, 1e-3)
    return 1.0 / (1.0 + chi / sig)


def _absorb(comp_unk, std, e_keV, e0=20.0, take_off=40.0):
    """C = k * f(standard) / f(sample) for one line.

    Both sides are corrected with the same μ/ρ table.  The standard's own matrix
    is used when its composition is known (a formula label such as "SiO2" or a
    mineral name such as "Di N1"); otherwise the standard is treated as its own
    element's oxide, which keeps the term small instead of dropping it.
    """
    fs = absorption_factor(comp_unk, e_keV, e0, take_off)
    cs = std.get("comp") if isinstance(std, dict) else None
    if not cs:
        label = std.get("label") if isinstance(std, dict) else (std or "")
        cs = standard_composition(label)
    if not cs and isinstance(std, dict):
        sym = None
        for s, row in EL.items():
            if row[0] == std.get("z"):
                sym = s
                break
        if sym and EL[sym][2]:
            cs = standard_composition(EL[sym][2])
    if not cs:
        # The standard's matrix is unknown: assume it matches the sample, i.e. no
        # correction.  Applying only half of the term (sample side) is worse than
        # applying none -- that is what used to bias every result.
        return 1.0
    return absorption_factor(cs, e_keV, e0, take_off) / fs if fs > 0 else 1.0


def quantify(counts, std, fwhm=120.0, allowed=None, elements=None, e0=20.0,
             take_off=40.0, absorption=None):
    """Integrate the lines of ``std`` and turn them into weight fractions.

    ``elements``: the symbols to quantify, in the order they should be reported.
    ``allowed``: set of atomic numbers the acquisition actually searched for
    (``inca_ipj.Spectrum.element_set()``) -- kept for callers that only have
    that.  Oxygen is always kept: it is needed for the absorption matrix and is
    dropped again by ``to_oxides``.

    ``absorption``: apply the matrix correction f(standard)/f(sample).  None
    means "as the settings say" (off by default, see quant.absorption_enabled).

    Line overlaps are handled by measuring every K-alpha *and* every K-beta and
    solving the small linear system that says how much of each line falls into
    each window (quant.measure_lines): without it Cr K-beta would be read as
    Mn K-alpha and Ti K-beta as V K-alpha, i.e. the result would invent elements
    the spectrum does not contain and inflate the ones it does.
    """
    if absorption is None:
        absorption = Q.absorption_enabled()
    syms = [s for s in (elements if elements is not None else std) if s in std]
    if not syms:
        return {}, {}
    components = [(s, EL[s][1], KB.get(s), KB_RATIO.get(s, 0.0)) for s in syms]
    measured = Q.measure_lines(counts, components, I.EV_PER_CH, I.ZERO_CH,
                               fwhm_ev=fwhm)
    areas = {s: measured.get(s, 0.0) for s in syms}
    raw = {sym: a * std[sym]["conc"] / std[sym]["area"]
           for sym, a in areas.items() if a > 0 and std[sym]["area"] > 0}
    if not raw:
        return {}, areas
    t = sum(raw.values())
    comp = {k: v / t for k, v in raw.items()}
    if not absorption:
        return comp, areas
    for _ in range(40):
        new = {s: raw[s] * _absorb(comp, std[s], std[s]["e"], e0, take_off)
               for s in raw}
        t = sum(new.values())
        new = {k: v / t for k, v in new.items()}
        if max(abs(new[k] - comp[k]) for k in new) < 1e-8:
            comp = new
            break
        comp = new
    return comp, areas


def labelled_elements(sp):
    """Symbols of the elements whose peaks INCA wrote into the spectrum.

    The list is what the spectrum panel draws as peak labels, so a result built
    from it is exactly the result for what the user sees on the plot.
    """
    out = []
    for _idx, name, _energy in sp.labels():
        sym = (name or "").strip()
        if sym in EL and sym not in out:
            out.append(sym)
    return out


def quantify_spectrum(sp, std, fwhm=120.0, absorption=None):
    """One spectrum -> (comp, oxides, details) with a consistent element list.

    ``details`` has three groups so the caller can explain the result instead of
    hiding what was left out:

        quantified  -- elements with a standard, measured and corrected
        no_standard -- labelled on the spectrum, but the calibration table has
                       no standard for them (nothing to divide by)
        no_oxide    -- quantified, but has no conventional oxide (C, O, F, Cl):
                       reported as the element
    """
    counts = sp.counts()
    labels = labelled_elements(sp)
    searched = sp.element_set()
    syms = []
    for sym in labels:
        if searched and EL[sym][0] not in searched:
            continue
        syms.append(sym)
    # a spectrum with no peak labels at all still gets the project's element list
    if not syms and searched:
        syms = [s for s, row in EL.items() if row[0] in searched]
    quantified = [s for s in syms if s in std]
    missing = [s for s in syms if s not in std]
    if "O" not in quantified and "O" in std:
        quantified.append("O")          # matrix for the absorption correction
    comp, areas = quantify(counts, std, fwhm=fwhm, elements=quantified,
                           absorption=absorption)
    ox = to_oxides(comp)
    # every element of the spectrum is accounted for: it either has an oxide in
    # the result or is listed as an element (C, O, F, Cl)
    no_oxide = [s for s in comp if EL[s][2] is None]
    return comp, ox, {"quantified": [s for s in quantified if s != "O"],
                      "no_standard": missing,
                      "no_oxide": no_oxide,
                      "areas": areas}


def to_oxides(comp):
    ox = {}
    for sym, c in comp.items():
        z, e, name, ncat, nox, mcat, mox = EL[sym]
        if name is None:
            continue
        ox[name] = ox.get(name, 0.0) + c * mox / (ncat * mcat)
    t = sum(ox.values())
    return {k: v / t for k, v in ox.items()} if t > 0 else {}


def is_failed(counts):
    """an acquisition that never got past the zero peak"""
    if not counts or len(counts) < 80:
        return True
    peak = max(counts[70:])
    return peak < 150 or peak < 0.08 * max(counts)


def analyse_project(project_path=None, std=None, progress=None, project=None,
                    cal_dir=None, detector=None, e0=None):
    """returns a list of records, one per spectrum (usable and failed)

    ``project`` may be an already-read inca_ipj.Project; otherwise the file is
    read here.  When ``std`` is None the standards table of the calibration
    profile is used, at the accelerating voltage found in the project.

    Each record carries ``elements`` -- the elements the acquisition searched
    for (empty when the project does not say).
    """
    p = project or I.Project(project_path)
    if std is None:
        if e0 is None:
            for _f, sp in p.all_spectra():
                e0 = sp.em_conditions().get("kV")
                if e0:
                    break
        std = load_standards(cal_dir=cal_dir, detector=detector, e0=e0)
    recs = []
    for s in p.samples:
        for f in s.fovs:
            for idx, sp in enumerate(f.spectra, 1):
                c = sp.counts()
                allowed = sp.element_set()
                rec = {"sample": s.name(), "site": f.name(), "fov_id": f.id,
                       "spec": sp.id, "index": idx,
                       "elements": sorted(_Z_SYMBOL.get(z, str(z))
                                          for z in (allowed or ())),
                       "total_counts": sum(c), "peak_counts": max(c) if c else 0}
                if is_failed(c):
                    rec["failed"] = True
                    recs.append(rec)
                    continue
                comp, ox, details = quantify_spectrum(sp, std)
                rec["failed"] = False
                rec["comp"] = comp
                rec["oxides"] = ox
                rec["no_oxides"] = not ox
                rec["found"] = details["quantified"]
                rec["no_standard"] = details["no_standard"]
                rec["no_oxide"] = details["no_oxide"]
                recs.append(rec)
                if progress:
                    progress("%s / %s" % (f.name(), sp.id))
    return recs


def summary_line(rec):
    if rec.get("failed"):
        return "неудачная съёмка"
    ox = rec.get("oxides") or {}
    missing = rec.get("no_standard") or []
    if not ox:
        el = ", ".join(rec.get("elements") or []) or "—"
        return "нет элементов, дающих оксиды (в проекте: %s)" % el
    parts = ["%s %.1f" % (k, ox[k] * 100) for k in OXIDE_ORDER
             if ox.get(k, 0) * 100 >= 0.5]
    if missing:
        parts.append("(нет стандарта: %s)" % ", ".join(missing))
    return ", ".join(parts)


def write_csv(recs, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["site", "spectrum", "n", "total_counts", "peak_counts"]
                   + OXIDE_ORDER + ["Total"])
        for r in recs:
            if r.get("failed") or r.get("no_oxides"):
                w.writerow([r["site"], r["spec"], r["index"], r["total_counts"],
                            r["peak_counts"]] + ["—"] * (len(OXIDE_ORDER) + 1))
                continue
            ox = r["oxides"]
            vals, tot = [], 0.0
            for k in OXIDE_ORDER:
                v = ox.get(k, 0.0) * 100
                tot += v
                vals.append("%.2f" % v)
            w.writerow([r["site"], r["spec"], r["index"], r["total_counts"],
                        r["peak_counts"]] + vals + ["%.2f" % tot])
    return path
