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
import os, sys, csv, math

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import inca_ipj as I
import quant as Q

# element: Z, Ka energy keV, oxide, cations in oxide, oxygens, cation mass, oxide mass
EL = {
    "O":  (8,  0.5249, None,    0, 0, 15.999, 15.999),
    "Na": (11, 1.0410, "Na2O",  2, 1, 22.990, 61.979),
    "Mg": (12, 1.2536, "MgO",   1, 1, 24.305, 40.304),
    "Al": (13, 1.4867, "Al2O3", 2, 3, 26.982, 101.961),
    "Si": (14, 1.7399, "SiO2",  1, 2, 28.086, 60.084),
    "K":  (19, 3.3133, "K2O",   2, 1, 39.098, 94.196),
    "Ca": (20, 3.6917, "CaO",   1, 1, 40.078, 56.077),
    "Ti": (22, 4.5109, "TiO2",  1, 2, 47.867, 79.866),
    "Mn": (25, 5.8988, "MnO",   1, 1, 54.938, 70.937),
    "Fe": (26, 6.4038, "FeO",   1, 1, 55.845, 71.844),
}

OXIDE_ORDER = ["SiO2", "TiO2", "Al2O3", "FeO", "MnO", "MgO", "CaO", "Na2O", "K2O"]

# symbols for reporting the element lists of the projects (not all are quantified)
_Z_SYMBOL = dict(enumerate(
    "- H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co "
    "Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te "
    "I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir "
    "Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu".split(), 0))

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
        n = 0
        for sym, (z, _e, *_r) in EL.items():
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
        out[sym] = {"z": z, "e": e,
                    "conc": (_as_float(r.get("Conc Std"), 0.0) or 0.0) / 100.0,
                    "area": _as_float(r.get("Norm Area Std"), 0.0) or 0.0,
                    "label": (r.get("Label") or "").strip(),
                    "date": r.get("Date") or "",
                    "det": _as_int(r.get("Detector Type"), 0)}
    return out


def _absorb(comp_unk, std_label, e_keV, e0=20.0, take_off=40.0):
    cs = STD_COMP.get(std_label)
    if not cs:
        return 1.0
    chi_u = Q.mac_mixture(comp_unk, e_keV) / math.sin(math.radians(take_off))
    chi_s = Q.mac_mixture(cs, e_keV) / math.sin(math.radians(take_off))
    sig = 2.39e5 / max(e0 ** 1.5 - e_keV ** 1.5, 1e-3)
    au = 1.0 / (1.0 + chi_u / sig)
    a_s = 1.0 / (1.0 + chi_s / sig)
    return a_s / au if au > 0 else 1.0


def quantify(counts, std, fwhm=120.0, allowed=None):
    """Integrate the lines of ``std`` and turn them into weight fractions.

    ``allowed``: set of atomic numbers the acquisition actually searched for
    (``inca_ipj.Spectrum.element_set()``).  Elements outside it are not
    integrated, so a line the operator never acquired cannot appear in the
    result.  Oxygen is always kept: it is needed for the absorption matrix and
    is dropped again by ``to_oxides``.
    """
    areas = {}
    for sym, s in std.items():
        if allowed is not None and sym != "O" and EL[sym][0] not in allowed:
            continue
        areas[sym] = Q.peak_area(counts, s["e"], I.EV_PER_CH, I.ZERO_CH, fwhm_ev=fwhm)[0]
    raw = {sym: a * std[sym]["conc"] / std[sym]["area"]
           for sym, a in areas.items() if a > 0 and std[sym]["area"] > 0}
    if not raw:
        return {}, areas
    t = sum(raw.values())
    comp = {k: v / t for k, v in raw.items()}
    for _ in range(30):
        new = {s: raw[s] * _absorb(comp, std[s]["label"], std[s]["e"]) for s in raw}
        t = sum(new.values())
        new = {k: v / t for k, v in new.items()}
        if max(abs(new[k] - comp[k]) for k in new) < 1e-8:
            comp = new
            break
        comp = new
    return comp, areas


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
                comp, areas = quantify(c, std, allowed=allowed)
                rec["failed"] = False
                rec["comp"] = comp
                rec["oxides"] = to_oxides(comp)
                rec["no_oxides"] = not rec["oxides"]
                recs.append(rec)
                if progress:
                    progress("%s / %s" % (f.name(), sp.id))
    return recs


def summary_line(rec):
    if rec.get("failed"):
        return "неудачная съёмка"
    ox = rec.get("oxides") or {}
    if not ox:
        el = ", ".join(rec.get("elements") or []) or "—"
        return "нет элементов, дающих оксиды (в проекте: %s)" % el
    parts = ["%s %.1f" % (k, ox[k] * 100) for k in OXIDE_ORDER
             if ox.get(k, 0) * 100 >= 0.5]
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
