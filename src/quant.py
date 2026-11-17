# -*- coding: utf-8 -*-
"""
quant.py -- quantitative EDS analysis from an INCA .ipj without INCA.

Pipeline
  1. peak integration with a local linear background (counts above bg)
  2. k-ratio against the XPP standards database (Norm Area Std / Conc Std)
  3. ZAF correction, iterated to a normalised total
  4. validation against standards of known composition

Physics tables (mass absorption coefficients, atomic weights, fluorescence
yields, line energies) come from xraylib/Chantler, pre-dumped by make_physics.py.

Calibration is *data*, not code
-------------------------------
Every instrument has its own standards measurement, so the calibration lives in
a separate folder and can be swapped without rebuilding the program:

    calibration\\
      mac.csv, elements.csv, lines.csv     physics, shared by every profile
      xpp_standards.csv                    the default profile's standards table
      profile.ini                          what the default profile is
      profiles\\
        msu-cpx-opx-amp-bt\\
          xpp_standards.csv                one table per analytical protocol
          profile.ini                      instrument / detector / notes

A profile's own folder may also hold its own physics CSVs; anything missing is
taken from the calibration root.  $NEWINCA_CALIB replaces the root.
"""
import os, sys, csv, math, configparser

HERE = os.path.dirname(os.path.abspath(__file__))

PROFILE_INI = "profile.ini"
SETTINGS_INI = "settings.ini"
STANDARDS_CSV = "xpp_standards.csv"


def app_dir():
    """folder the program lives in (next to the .exe when frozen)"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(HERE)


# --------------------------------------------------------------------------- #
#  calibration profiles
# --------------------------------------------------------------------------- #
class Profile:
    """One calibration set (one standards measurement on one instrument)."""

    def __init__(self, slug, path, meta=None):
        meta = meta or {}
        self.slug = slug
        self.path = os.path.abspath(path)
        self.title = meta.get("title") or ("по умолчанию" if slug == "default"
                                           else slug)
        self.instrument = meta.get("instrument", "")
        self.family = (meta.get("family") or "").lower()
        self.detector_serial = (meta.get("detector_serial") or "").strip()
        self.detector_model = (meta.get("detector_model") or "").strip()
        self.e0 = meta.get("e0", "")
        self.notes = meta.get("notes", "")
        self.source = meta.get("source", "")
        self.is_default = str(meta.get("default", "")).strip().lower() in \
            ("1", "yes", "true", "да")

    def __repr__(self):
        return "<Profile %s %r>" % (self.slug, self.title)

    @property
    def standards_path(self):
        return os.path.join(self.path, STANDARDS_CSV)

    def has_standards(self):
        return os.path.isfile(self.standards_path)

    def physics_dir(self):
        """profile folder when it carries its own physics tables, else the root"""
        if os.path.isfile(os.path.join(self.path, "mac.csv")):
            return self.path
        return calibration_root()

    def matches_detector(self, serial="", model=""):
        if serial and self.detector_serial and serial == self.detector_serial:
            return True
        if model and self.detector_model and \
                self.detector_model.lower() in model.lower():
            return True
        return False


def _read_meta(path):
    if not os.path.isfile(path):
        return {}
    cp = configparser.ConfigParser()
    try:
        cp.read(path, encoding="utf-8-sig")
    except Exception:
        return {}
    if not cp.has_section("profile"):
        return {}
    return dict(cp.items("profile"))


def calibration_root():
    """Locate the calibration folder itself ($NEWINCA_CALIB wins).

    The data is deliberately NOT embedded in the program: it is instrument data
    that must be replaceable without a rebuild.
    """
    cands = []
    env = os.environ.get("NEWINCA_CALIB")
    if env:
        # an explicit override is honoured even if it is empty: the program then
        # says that there is no calibration, which is easier to diagnose than a
        # silent fallback to the bundled tables
        if os.path.isdir(env):
            return os.path.abspath(env)
        cands.append(env)
    if getattr(sys, "frozen", False):
        cands.append(os.path.join(os.path.dirname(sys.executable), "calibration"))
    if sys.argv and sys.argv[0]:
        cands.append(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])),
                                  "calibration"))
    cands.append(os.path.join(HERE, "..", "calibration"))
    cands.append(os.path.join(HERE, "calibration"))
    for c in cands:
        if not c or not os.path.isdir(c):
            continue
        if (os.path.exists(os.path.join(c, "mac.csv"))
                or os.path.exists(os.path.join(c, STANDARDS_CSV))):
            return os.path.abspath(c)
    return os.path.abspath(cands[-1])


def profiles(root=None):
    """[Profile] -- the default one first, then calibration\\profiles\\*"""
    root = root or calibration_root()
    out = [Profile("default", root, _read_meta(os.path.join(root, PROFILE_INI)))]
    pdir = os.path.join(root, "profiles")
    if os.path.isdir(pdir):
        for name in sorted(os.listdir(pdir)):
            d = os.path.join(pdir, name)
            if os.path.isdir(d) and os.path.isfile(os.path.join(d, STANDARDS_CSV)):
                meta = _read_meta(os.path.join(d, PROFILE_INI))
                out.append(Profile(name, d, meta))
    return out


def profile(slug, root=None):
    if not slug:
        return None
    if slug == "default":
        return profiles(root)[0]
    for p in profiles(root)[1:]:
        if p.slug == slug or p.title == slug:
            return p
    return None


# ---- persisted user choice ------------------------------------------------ #
def _settings_path():
    return os.path.join(app_dir(), SETTINGS_INI)


def settings():
    cp = configparser.ConfigParser()
    try:
        cp.read(_settings_path(), encoding="utf-8-sig")
    except Exception:
        pass
    return cp


def _save_settings(cp):
    try:
        with open(_settings_path(), "w", encoding="utf-8") as fh:
            cp.write(fh)
        return True
    except Exception:
        return False


def set_active_profile(slug, remember_for=""):
    """Remember the selected profile (and, optionally, as the instrument's own)."""
    cp = settings()
    if not cp.has_section("calibration"):
        cp.add_section("calibration")
    cp.set("calibration", "active", slug or "default")
    if remember_for:
        key = "detector_" + "".join(ch for ch in remember_for if ch.isalnum())
        cp.set("calibration", key, slug or "default")
    return _save_settings(cp)


def saved_for_detector(serial):
    if not serial:
        return ""
    key = "detector_" + "".join(ch for ch in serial if ch.isalnum())
    return settings().get("calibration", key, fallback="")


def active_profile(root=None):
    """The profile the program currently calibrates with."""
    slug = settings().get("calibration", "active", fallback="")
    p = profile(slug, root)
    if p is not None and p.has_standards():
        return p
    ps = profiles(root)
    return ps[0]


def calibration_dir(root=None):
    """Folder of the active profile (kept for compatibility)."""
    return active_profile(root).path


def standards_csv(root=None):
    """Path of the standards table actually used."""
    p = active_profile(root)
    if p.has_standards():
        return p.standards_path
    return os.path.join(calibration_root(), STANDARDS_CSV)


def physics_file(name):
    """Physics CSV: profile folder first, then the calibration root."""
    for d in (active_profile().physics_dir(), calibration_root()):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return os.path.join(calibration_root(), name)


def suggest_for(fmt, root=None):
    """Pick the calibration profile that belongs to a project's instrument.

    fmt: inca_ipj.ProjectFormat (or anything with detector_serial / detector_model
    / family).  Returns (Profile, reason-in-Russian).
    """
    ps = profiles(root)
    default = ps[0]
    if fmt is None:
        return default, "по умолчанию"
    serial = getattr(fmt, "detector_serial", "") or ""
    model = getattr(fmt, "detector_model", "") or ""
    family = getattr(fmt, "family", "") or ""

    remembered = profile(saved_for_detector(serial), root) if serial else None
    if remembered is not None and remembered.has_standards():
        return remembered, "сохранённый выбор для детектора S/N %s" % serial

    if serial and default.detector_serial == serial:
        return default, "серийный номер детектора S/N %s" % serial

    cands = [p for p in ps[1:] if serial and p.detector_serial == serial]
    reason = "серийный номер детектора S/N %s" % serial
    if not cands and model:
        cands = [p for p in ps[1:]
                 if p.detector_model and p.detector_model.lower() in model.lower()]
        reason = "модель детектора %s" % model
    if not cands and family and not (serial or model):
        cands = [p for p in ps[1:] if p.family == family]
        reason = "семейство проекта (%s)" % family
    if not cands:
        return default, "профиль по умолчанию"
    marked = [p for p in cands if p.is_default]
    return (marked or cands)[0], reason


def profile_lines(root=None):
    """human readable list of profiles, for the GUI/CLI"""
    out = []
    active = active_profile(root)
    for p in profiles(root):
        mark = "*" if p.slug == active.slug else " "
        det = ", ".join(x for x in (p.detector_model, p.detector_serial) if x)
        out.append("%s %-24s %-40s %s" % (mark, p.slug, p.title, det))
    return out


# --------------------------------------------------------------------------- #
#  physics tables
# --------------------------------------------------------------------------- #
PHYS = None

_TAB = {}


def _load():
    global PHYS
    if _TAB:
        return _TAB
    PHYS = active_profile().physics_dir()
    mac = {}
    with open(physics_file("mac.csv"), encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            mac[(int(r["Z"]), round(float(r["energy_keV"]), 3))] = float(r["mu_over_rho_cm2_per_g"])
    el = {}
    with open(physics_file("elements.csv"), encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            el[int(r["Z"])] = r
    _TAB["mac"] = mac
    _TAB["el"] = el
    return _TAB


def reset_cache():
    """Forget cached tables after the calibration profile changed."""
    global PHYS
    PHYS = None
    _TAB.clear()



def atomic_weight(z):
    return float(_load()["el"][z]["atomic_weight"])


def fluor_yield_k(z):
    v = _load()["el"].get(z, {}).get("fluor_yield_K", "")
    return float(v) if v else 0.0


def mac(z_absorber, energy_keV):
    """mass absorption coefficient, cm2/g.  nearest tabulated energy."""
    t = _load()["mac"]
    e = round(energy_keV, 3)
    if (z_absorber, e) in t:
        return t[(z_absorber, e)]
    # nearest
    ks = [k for k in t if k[0] == z_absorber]
    if not ks:
        return 0.0
    k = min(ks, key=lambda kk: abs(kk[1] - e))
    return t[k]


def mac_mixture(comp_wt, energy_keV):
    """comp_wt: {Z: weight fraction}"""
    return sum(w * mac(z, energy_keV) for z, w in comp_wt.items() if w > 0)


# --------------------------------------------------------------------------- #
#  peak integration
# --------------------------------------------------------------------------- #
MIN_CH = 30          # channels below this belong to the zero peak / pre-amp noise


def background(counts, deg=3, iters=12, k=1.5, min_ch=MIN_CH):
    """Continuum estimate: iterative polynomial fit in log(counts) vs log(channel)
    with sigma clipping, so peak channels are rejected as outliers.

    This is the standard way to model the EDS bremsstrahlung continuum: it
    cannot be poisoned by a neighbouring peak, and it follows the steep
    low-energy falloff as a power law.
    """
    import numpy as np
    c = np.asarray(counts, dtype=float)
    idx = np.arange(len(c), dtype=float)
    m = idx >= min_ch
    x = np.log10(idx[m])
    y = np.log10(np.maximum(c[m], 1.0))
    use = np.ones(x.shape, dtype=bool)
    for _ in range(iters):
        if use.sum() < deg + 3:
            break
        co = np.polyfit(x[use], y[use], deg)
        r = y - np.polyval(co, x)
        sig = 1.4826 * np.median(np.abs(r[use] - np.median(r[use])))
        sig = max(sig, 2e-3)
        new = r < k * sig
        if new.sum() < deg + 3 or new.sum() == use.sum():
            use = new
            break
        use = new
    co = np.polyfit(x[use], y[use], deg)
    bg = np.zeros(len(c))
    bg[m] = 10.0 ** np.polyval(co, x)
    bg[~m] = c[~m]
    return np.maximum(bg, 0.0)


def peak_area(counts, e_keV, ev_per_ch, zero_ch, fwhm_ev=120.0,
              int_f=0.55, excl_f=1.15, span_f=4.5, deg=2, iters=8):
    """Integrate one peak above a *locally fitted* continuum.

    A low-order polynomial is fitted to the spectrum within +-span_f FWHM of
    the line, excluding the peak window itself, with iterative sigma clipping
    so neighbouring lines are rejected instead of being absorbed into the
    background.  The residual of the peak window above that fit is the area.
    """
    import numpy as np
    c = np.asarray(counts, dtype=float)
    n = len(c)
    e_ev = e_keV * 1000.0
    c0 = e_ev / ev_per_ch + zero_ch          # E = a*(ch - zero)  =>  ch = E/a + zero
    f = fwhm_ev / ev_per_ch                       # FWHM in channels
    half = int_f * fwhm_ev / ev_per_ch
    excl = excl_f * fwhm_ev / ev_per_ch
    span = span_f * fwhm_ev / ev_per_ch
    lo, hi = int(round(c0 - span)), int(round(c0 + span))
    plo, phi = int(round(c0 - half)), int(round(c0 + half))
    lo = max(lo, MIN_CH)
    hi = min(hi, n - 2)
    if plo < MIN_CH or phi > n - 2 or hi - lo < 12:
        return 0.0, 0.0
    x = np.arange(lo, hi + 1, dtype=float)
    y = c[lo:hi + 1]
    keep = np.abs(x - c0) > excl                   # everything but the peak
    if keep.sum() < deg + 4:
        return 0.0, 0.0
    use = keep.copy()
    for _ in range(iters):
        if use.sum() < deg + 3:
            break
        co = np.polyfit(x[use] - c0, y[use], deg)
        r = y - np.polyval(co, x - c0)
        sig = 1.4826 * np.median(np.abs(r[use] - np.median(r[use])))
        sig = max(sig, 1.0)
        new = keep & (r < 2.0 * sig)               # drop positive excursions = lines
        if new.sum() < deg + 3 or new.sum() == use.sum():
            use = new
            break
        use = new
    co = np.polyfit(x[use] - c0, y[use], deg)
    net = 0.0
    gross = 0.0
    for i in range(plo, phi + 1):
        net += c[i] - np.polyval(co, i - c0)
        gross += c[i]
    return net, math.sqrt(max(gross, 1.0))


def find_peaks(counts, ev_per_ch, zero_ch, thresh=0.04, min_ch=MIN_CH):
    """local maxima above thresh * (largest count above min_ch); returns [(E_keV, counts)]"""
    seg = counts[min_ch:]
    if not seg:
        return []
    mx = max(seg)
    out = []
    for i in range(max(min_ch, 1), len(counts) - 1):
        c = counts[i]
        if c >= thresh * mx and c > counts[i - 1] and c >= counts[i + 1]:
            out.append(((i - zero_ch) * ev_per_ch / 1000.0, c))
    return out


def measure(spectrum, elements, ev_per_ch, zero_ch, **kw):
    """elements: list of (symbol, Z, line_energy_keV)
    returns {symbol: area}"""
    counts = spectrum
    out = {}
    for sym, z, e in elements:
        a, err, bg, npk, nbg = peak_area(counts, e, ev_per_ch, zero_ch, **kw)
        out[sym] = (a, err)
    return out


# --------------------------------------------------------------------------- #
#  ZAF
# --------------------------------------------------------------------------- #
TAKE_OFF_DEG = 40.0


def stopping_power(comp, e0_kev):
    """S = sum C_j Z_j / A_j * (1/J_j) ln(...)  -- Castaing/Bishop form.
    returns relative stopping power (arbitrary but consistent units)."""
    s = 0.0
    for z, c in comp.items():
        if c <= 0:
            continue
        j = 11.5 * z + 0.001                # mean ionisation potential, eV  (Berger-Seltzer-ish)
        s += c * z / atomic_weight(z) * (1.0 / (j * 1e-3)) * math.log(e0_kev / (j * 1e-3))
    return s


def backscatter_factor(comp, e0_kev):
    """Duncumb-Reed style: R = 1 - eta*(1 - ...); use R = 1 - 1.8e-3*(Zbar-...) """
    zbar = sum(c * z for z, c in comp.items() if c > 0)
    abar = sum(c * atomic_weight(z) for z, c in comp.items() if c > 0)
    # Reed (1975) approximation for the backscatter loss factor
    eta = -0.0254 + 0.016 * zbar - 1.86e-4 * zbar ** 2 + 8.3e-7 * zbar ** 3
    return 1.0 - eta


def absorption_factor(comp, e_keV, e0_kev, take_off_deg=TAKE_OFF_DEG, sigma=None):
    """f(chi) with the classic exponential depth model  f = 1/(1+chi/sigma)."""
    mu = mac_mixture(comp, e_keV)
    chi = mu / math.sin(math.radians(take_off_deg))
    if sigma is None:
        sigma = 2.39e5 / (e0_kev ** 1.5 - e_keV ** 1.5)
    return 1.0 / (1.0 + chi / sigma)


def zaf(comp_unk, comp_std, e_keV, e0_kev=20.0, take_off_deg=TAKE_OFF_DEG):
    """returns the factor to multiply the k-ratio by, i.e. C = k * ZAF_rel^-1 """
    su, ss = stopping_power(comp_unk, e0_kev), stopping_power(comp_std, e0_kev)
    ru, rs = backscatter_factor(comp_unk, e0_kev), backscatter_factor(comp_std, e0_kev)
    z = (su / ru) / (ss / rs) if ss and rs else 1.0
    au = absorption_factor(comp_unk, e_keV, e0_kev, take_off_deg)
    as_ = absorption_factor(comp_std, e_keV, e0_kev, take_off_deg)
    a = au / as_ if as_ else 1.0
    return z, a


def quantify(areas, std_info, e0_kev=20.0, max_iter=60, absorption=True):
    """areas    : {sym: area}
    std_info : {sym: dict(conc, area, z, e_keV, comp_std)}
    returns {sym: wt fraction} normalised to 1
    """
    raw = {}
    for sym, (a, err) in areas.items():
        if sym not in std_info or a <= 0:
            continue
        s = std_info[sym]
        raw[sym] = a * s["conc"] / s["area"]
    if not raw:
        return {}
    tot = sum(raw.values())
    comp = {k: v / tot for k, v in raw.items()}
    for _ in range(max_iter):
        new = {}
        for sym, k in raw.items():
            s = std_info[sym]
            zc, ac = zaf({z: c for z, c in comp.items()},
                         s["comp_std"], s["e_keV"], e0_kev) if absorption else (1.0, 1.0)
            new[sym] = k * (zc * ac)
        t = sum(new.values())
        new = {k: v / t for k, v in new.items()}
        if max(abs(new[k] - comp[k]) for k in new) < 1e-7:
            comp = new
            break
        comp = new
    return comp
