# -*- coding: utf-8 -*-
"""
inca_ipj.py -- read Oxford Instruments INCA project files (.ipj/.ipl) WITHOUT
INCA itself.

An .ipj is a Microsoft Compound File (OLE2 / CFBF) container: a tree of
storages (folders) and streams (leaf data).

Why this module looks names up instead of trusting fixed paths
---------------------------------------------------------------
The *leaf* stream names are stable across INCA versions and instruments:
``IIncaProjectInfo``, ``IFOVInfo``, ``ISampleInfo``, ``XrayData/Spectrum``,
``ChordList/ChordList``, ``GreyImage/micromap``, ``EMConditions``, ``Elements``,
``PeakLabels`` ...  What is NOT stable is everything around them:

* which hardware wrote the file -- an ISIS/WDS Camebax writes
  ``XrayDetector/ISIS_Processor``, an XStream (XGT2, X-Max) instrument writes
  ``XrayDetector/XStreamProcessor`` and stores ``XGT2Data``;
* how deep the tree is and which intermediate storages exist (``Samples``
  may be absent altogether, ``Images`` may be empty, spectra may sit directly
  in a storage instead of in ``SPE...`` sub-storages);
* whether an image is 8-bit (tag 0x0011, one byte per pixel) or 16-bit
  (tag 0x0002, two bytes per pixel) -- both occur in real projects;
* whether the analysis positions in ``ChordList`` were written at all (an
  INCA project that only kept spectra has a 10-byte, empty ChordList).

So the reader indexes the tree once and resolves every object by *role*: a
list of known basenames, matched case-insensitively at any depth.  A project
that has no images, no coordinates or no samples is legal and is read as such;
nothing is invented.

The reference layout (INCA 6.1.14, Camebax, ED detector):

  Root Entry
    IIncaProjectInfo          project name, operator
    Samples
      SMP<id>                 one sample
        ISampleInfo           sample name / description
        FOVs
          FOV<id>             one field of view = one "point analysis" site
            IFOVInfo          site name
            Images
              SEI<id>         one electron image
                ImageData     48 B   display/scale parameters
                GreyImage     micromap (+ label, EMConditions)
                OverlayData   micromap (+ label, EMConditions)
                annotation
            Spectra
              SPE<id>         one EDS spectrum
                XrayData/Spectrum   10-byte header + N x uint32 counts
                ChordList/ChordList where the analysis sits on the image
                Elements / PeakLabels / EMConditions / ...
                XrayDetector/ED_Detector, ISIS_Processor | XStreamProcessor
            CalibSpectrum    the stored calibration spectrum for that site
            WDSpectra        wavelength-dispersive spectra (Camebax; may be empty)

Usage:
    python inca_ipj.py list   <file.ipj>
    python inca_ipj.py export <file.ipj> <outdir>
"""
import os, re, sys, struct

FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE


# --------------------------------------------------------------------------- #
#  CFBF container
# --------------------------------------------------------------------------- #
class DirEntry:
    __slots__ = ("sid", "name", "type", "left", "right", "child", "start",
                 "size", "ctime", "mtime")

    def __init__(self, sid, raw):
        (name_len,) = struct.unpack_from("<H", raw, 64)
        self.sid = sid
        self.name = raw[0:max(0, name_len - 2)].decode("utf-16-le", "replace")
        self.type = raw[66]
        self.left, self.right, self.child = struct.unpack_from("<III", raw, 68)
        self.ctime, self.mtime = struct.unpack_from("<QQ", raw, 100)
        self.start, = struct.unpack_from("<I", raw, 116)
        self.size, = struct.unpack_from("<Q", raw, 120)

    @property
    def kind(self):
        return {0: "unknown", 1: "storage", 2: "stream", 5: "root"}.get(self.type, "?")

    def __repr__(self):
        return "<%s %r size=%d>" % (self.kind, self.name, self.size)


class CFBF:
    """Read-only Microsoft Compound File Binary Format container."""

    def __init__(self, path):
        with open(path, "rb") as f:
            self.data = f.read()
        d = self.data
        if d[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            raise ValueError("not an OLE2/CFBF file")
        self.minor, self.major = struct.unpack_from("<HH", d, 24)
        self.sector_size = 1 << struct.unpack_from("<H", d, 30)[0]
        self.mini_size = 1 << struct.unpack_from("<H", d, 32)[0]
        (self.num_fat,) = struct.unpack_from("<I", d, 44)
        (self.first_dir,) = struct.unpack_from("<I", d, 48)
        (self.mini_cutoff,) = struct.unpack_from("<I", d, 56)
        (self.first_minifat,) = struct.unpack_from("<I", d, 60)
        (self.first_difat,) = struct.unpack_from("<I", d, 68)

        difat = list(struct.unpack_from("<109I", d, 76))
        s = self.first_difat
        guard = 0
        while s not in (ENDOFCHAIN, FREESECT) and s < 1 << 30 and guard < 100000:
            per = self.sector_size // 4 - 1
            v = struct.unpack_from("<%dI" % (self.sector_size // 4), d, self._off(s))
            difat.extend(v[:per])
            s = v[per]
            guard += 1
        difat = [x for x in difat if x not in (FREESECT, ENDOFCHAIN)]

        fat = []
        for fs in difat[:self.num_fat]:
            fat.extend(struct.unpack_from("<%dI" % (self.sector_size // 4),
                                          d, self._off(fs)))
        self.fat = fat

        mf = []
        for s in self._chain(self.first_minifat, fat):
            mf.extend(struct.unpack_from("<%dI" % (self.sector_size // 4),
                                         d, self._off(s)))
        self.minifat = mf

        dirdata = b"".join(d[self._off(s):self._off(s) + self.sector_size]
                           for s in self._chain(self.first_dir, fat))
        self.entries = [DirEntry(i, dirdata[i * 128:(i + 1) * 128])
                        for i in range(len(dirdata) // 128)]
        self.root = self.entries[0]
        self.ministream = b""
        if self.root.size and self.root.start not in (ENDOFCHAIN, FREESECT):
            self.ministream = b"".join(d[self._off(s):self._off(s) + self.sector_size]
                                       for s in self._chain(self.root.start, fat))
        self.paths = []          # list of (tuple(path), DirEntry)
        self._walk(0, ())

    @property
    def version_label(self):
        return "OLE2/CFBF v%d.%d, %d-byte sectors" % (self.major, self.minor,
                                                      self.sector_size)

    def _off(self, sect):
        return (sect + 1) * self.sector_size

    @staticmethod
    def _chain(start, fat):
        out, seen, s = [], set(), start
        while s not in (ENDOFCHAIN, FREESECT) and s < len(fat) and s not in seen:
            seen.add(s)
            out.append(s)
            s = fat[s]
        return out

    def _walk(self, sid, path):
        seen, stack, kids = set(), [sid], []
        while stack:
            s = stack.pop()
            if s in seen or s >= len(self.entries) or s == FREESECT:
                continue
            seen.add(s)
            e = self.entries[s]
            kids.append(e)
            stack.append(e.left)
            stack.append(e.right)
        kids.sort(key=lambda x: x.name.lower())
        for e in kids:
            p = path + (e.name,)
            self.paths.append((p, e))
            if e.child != FREESECT and e.type in (1, 5):
                self._walk(e.child, p)

    def stream_bytes(self, entry):
        if not entry.size:
            return b""
        if entry.size < self.mini_cutoff:
            out = b"".join(self.ministream[s * self.mini_size:(s + 1) * self.mini_size]
                           for s in self._chain(entry.start, self.minifat))
            return out[:entry.size]
        out = b"".join(self.data[self._off(s):self._off(s) + self.sector_size]
                       for s in self._chain(entry.start, self.fat))
        return out[:entry.size]

    def find(self, suffix):
        """first stream whose path ends with the given names"""
        want = tuple(suffix.split("/"))
        for p, e in self.paths:
            if e.type == 2 and p[-len(want):] == want:
                return self.stream_bytes(e)
        return None


# --------------------------------------------------------------------------- #
#  INCA record helpers  (MFC-CArchive style: dword version, dword tag, strings)
# --------------------------------------------------------------------------- #
def cp1251_runs(data, minlen=2):
    """printable cp1251 / ASCII runs inside a small metadata stream"""
    out, cur = [], bytearray()
    for b in data:
        if b == 0 or (b < 0x20 and b not in (9, 10, 13)):
            if len(cur) >= minlen:
                out.append(cur.decode("cp1251", "replace"))
            cur = bytearray()
        else:
            cur.append(b)
    if len(cur) >= minlen:
        out.append(cur.decode("cp1251", "replace"))
    return out


def first_text_run(info_bytes, minlen=3):
    """IFOVInfo / ISampleInfo -> the first real text field (skips numeric noise)"""
    for r in cp1251_runs(info_bytes, minlen):
        if any(ch.isalpha() for ch in r):
            return r.strip()
    return ""


def read_named(info_bytes):
    return first_text_run(info_bytes)


def parse_peak_labels(data):
    """PeakLabels stream -> [(kind_id, element, energy_eV), ...]"""
    if len(data) < 8:
        return []
    ver, cnt = struct.unpack_from("<II", data, 0)
    off, out = 8, []
    for _ in range(cnt):
        if off + 4 > len(data):
            break
        eid, ln = struct.unpack_from("<HH", data, off)
        off += 4
        name = data[off:off + ln].decode("cp1251", "replace")
        off += ln
        if off + 4 > len(data):
            break
        en, = struct.unpack_from("<f", data, off)
        off += 4
        out.append((eid, name, en))
    return out


def parse_information(data):
    """Spectrum `Information` stream -> acquisition timing and label.

    Layout measured on this build:
        0x00 dword  version (4)
        0x10 dword  accelerating voltage, kV (20)
        0x26 double live time, s
        0x2E double real time, s
        0x3A double OLE automation date
        0x44 text   running label ("  1")
    """
    out = {}
    if len(data) < 0x40:
        return out
    try:
        out["version"] = struct.unpack_from("<I", data, 0)[0]
        out["kv"] = struct.unpack_from("<I", data, 0x10)[0]
    except Exception:
        pass
    for key, off in (("live_s", 0x26), ("real_s", 0x2E)):
        try:
            v = struct.unpack_from("<d", data, off)[0]
            if 0 < v < 1e6:
                out[key] = v
        except Exception:
            pass
    try:
        days = struct.unpack_from("<d", data, 0x3A)[0]
        if 20000 < days < 80000:                    # OLE date, 1899-12-30 epoch
            import datetime
            out["date"] = (datetime.datetime(1899, 12, 30)
                           + datetime.timedelta(days=days))
    except Exception:
        pass
    tail = cp1251_runs(data[0x40:], 1)
    if tail:
        out["label"] = tail[-1]
    return out


def parse_spectrum(data):
    """XrayData/Spectrum -> (channel counts list, header info)

    10-byte header, then uint32 counts (little endian).  The number of channels
    follows from the stream length (2048 in every 20 keV / 2K project seen so
    far, but a 1K or 4K acquisition would simply give a different N).
    """
    if len(data) < 10:
        return [], {}
    ver, a, b, c = struct.unpack_from("<IHHH", data, 0)
    n = (len(data) - 10) // 4
    if n <= 0:
        return [], {"version": ver}
    counts = list(struct.unpack_from("<%dI" % n, data, 10))
    return counts, {"version": ver, "header": (a, b, c), "channels": n}


def parse_em_conditions(data):
    """EMConditions -> {'kV': 20.0} (plus the offset it was found at).

    The record is 219 bytes in both dialects; the accelerating voltage is a
    float at 0x2C, but the neighbouring fields move between the ISIS and the
    XStream writer, so a plausible value is looked up at a few offsets rather
    than trusted blindly.
    """
    out = {}
    if len(data) < 0x20:
        return out
    for off in (0x2C, 0x28, 0x30, 0x24, 0x34):
        if off + 4 > len(data):
            continue
        try:
            v = struct.unpack_from("<f", data, off)[0]
        except Exception:
            continue
        if 0.5 <= v <= 60.0 and abs(v - round(v)) < 1e-3:
            out["kV"] = round(v, 3)
            out["kV_offset"] = off
            break
    return out


def parse_ed_detector(data):
    """XrayDetector/ED_Detector -> {'version', 'serial', 'model'}.

    Observed layouts (both start with a dword version, then a NUL-padded
    cp1251 serial):

        09 00 00 00 | "327B5A81E" 00 ...                      (ISIS/Camebax)
        09 00 00 00 | "000000824" 00 ... .. float 35.0 .. 0B 00 "X-Max 7.ini"
                                                             (XStream/X-Max)
    """
    out = {"version": None, "serial": "", "model": ""}
    if len(data) >= 4:
        out["version"], = struct.unpack_from("<I", data, 0)
    if len(data) >= 8:
        raw = data[4:24].split(b"\x00")[0]
        txt = raw.decode("cp1251", "replace").strip()
        if txt and all(ch.isalnum() or ch in "-_ " for ch in txt):
            out["serial"] = txt
    m = re.search(rb"([\x20-\x7e]{3,40}\.ini)", data)
    if m:
        out["model"] = m.group(1).decode("ascii", "replace")
    return out


def parse_xray_detector(data):
    """XrayDetector/XrayDetector -> {'version', 'family_id'}.

    The last dword is the detector *family* as INCA numbers it: 1 on the ISIS
    (Camebax) detector, 3 and 4 on XStream detectors (4 = X-Max).
    """
    out = {"version": None, "family_id": None}
    if len(data) >= 4:
        out["version"], = struct.unpack_from("<I", data, 0)
    if len(data) >= 12:
        out["family_id"], = struct.unpack_from("<I", data, 8)
    return out


FAMILY_NAMES = {1: "isis", 3: "xstream", 4: "xstream"}

# The "Elements" stream is one dword per atomic number, shifted by 2: the flag
# for element Z sits at index Z+2 (verified on 444 spectra of six projects --
# every element INCA labelled as a peak is present in the table, and the table
# usually holds more entries, i.e. the elements the acquisition searched for).
ELEMENTS_INDEX_OFFSET = 2


def parse_elements_table(data):
    """Elements stream -> {Z: flags} for the elements the acquisition asked for."""
    if not data or len(data) < 4 * 8:
        return {}
    n = len(data) // 4
    out = {}
    for z in range(1, n - ELEMENTS_INDEX_OFFSET):
        v, = struct.unpack_from("<I", data, 4 * (z + ELEMENTS_INDEX_OFFSET))
        if v:
            out[z] = v
    return out


# --------------------------------------------------------------------------- #
#  images
# --------------------------------------------------------------------------- #
# GreyImage/micromap header (26 bytes, 0x1A) followed by the raster and a
# constant 222-byte trailer:
#
#   0x00 dword   version (8)
#   0x04 dword   flags
#   0x08 double  display level (255.0 for 8-bit; 26335.0 / 14760.0 for 16-bit)
#   0x10 dword   width
#   0x14 dword   height
#   0x18 word    pixel format: 0x0011 = 8 bit, 0x0002 = 16 bit
#   0x1A         width*height*(1|2) bytes, little endian grey
#   ...          constant 222-byte trailer
#
# Reading the raster from byte 248 instead of 26 shifts it by exactly 222 bytes
# and wraps the left band onto the right edge; that bug is fixed here.
IMAGE_HEADER = 0x1A
IMAGE_TRAILER = 222
PIXEL_FORMATS = {0x0011: 8, 0x0002: 16}


class Micromap:
    """One decoded ``micromap`` stream: 8- or 16-bit greyscale raster.

    ``payload`` is exactly ``width*height*bits/8`` bytes.  ``exact`` is False
    when the stream length did not match the header (the raster is then clipped
    or padded rather than silently shifted).
    """

    def __init__(self, raw):
        self.raw = raw or b""
        self.width = 0
        self.height = 0
        self.bits = 8
        self.header = IMAGE_HEADER
        self.trailer = 0
        self.level = None
        self.tag = None
        self.payload = b""
        self.exact = False
        self._parse()

    def _parse(self):
        d = self.raw
        if len(d) < 0x1C:
            return
        w, h = struct.unpack_from("<II", d, 0x10)
        if not (0 < w <= 65535 and 0 < h <= 65535):
            return
        self.width, self.height = w, h
        try:
            self.level, = struct.unpack_from("<d", d, 8)
        except Exception:
            self.level = None
        self.tag, = struct.unpack_from("<H", d, 0x18)
        pix = w * h
        n = len(d)

        bits = PIXEL_FORMATS.get(self.tag)
        if bits is None:                       # unknown tag: infer from length
            bits = 2 if n >= IMAGE_HEADER + 2 * pix else 1
        self.bits = bits
        nbytes = pix * (bits // 8)

        # canonical header first; only if the raster clearly does not fit there
        # (a different writer put more metadata in front) locate it by length.
        hdr = IMAGE_HEADER
        if n >= hdr + nbytes:
            if n - hdr - nbytes > 4096:
                guess = n - nbytes - IMAGE_TRAILER
                if 0 <= guess <= 4096:
                    hdr = guess
        self.header = hdr
        self.trailer = max(0, n - hdr - nbytes)
        self.payload = d[hdr:hdr + nbytes]
        if len(self.payload) < nbytes:          # truncated stream: pad, do not shift
            self.payload = self.payload + b"\x00" * (nbytes - len(self.payload))
        self.exact = (n - hdr - nbytes) == IMAGE_TRAILER

    @property
    def ok(self):
        return bool(self.width and self.height and self.payload)

    @property
    def shape(self):
        return (self.width, self.height)

    def __repr__(self):
        return "<Micromap %dx%d %d-bit>" % (self.width, self.height, self.bits)

    def to_pil(self):
        """PIL image, mode 'L', display-scaled.

        8-bit data is used as is.  16-bit data (Camebax "recovery" projects,
        XStream installations) is mapped to 0..255 through the display level
        stored in the header when that level is meaningful, otherwise through a
        0.1/99.9 percentile stretch.
        """
        from PIL import Image as PILImage
        if not self.ok:
            raise ValueError("not a decodable micromap")
        if self.bits == 8:
            return PILImage.frombytes("L", (self.width, self.height), self.payload)

        import numpy as np
        a = np.frombuffer(self.payload, dtype="<u2").reshape(self.height, self.width)
        level = self.level if self.level and self.level > 255 else None
        if level:
            scale = 255.0 / float(level)
            img = np.clip(a.astype(np.float32) * scale, 0.0, 255.0).astype("uint8")
        else:
            lo = float(np.percentile(a, 0.1))
            hi = float(np.percentile(a, 99.9))
            if hi <= lo:
                lo, hi = float(a.min()), float(a.max())
            if hi <= lo:
                hi = lo + 1.0
            img = np.clip((a.astype(np.float32) - lo) * (255.0 / (hi - lo)),
                          0.0, 255.0).astype("uint8")
        return PILImage.frombytes("L", (self.width, self.height), img.tobytes())


def parse_image(data):
    """GreyImage/micromap -> Micromap (or None when the stream is too short)."""
    if not data or len(data) < 0x1C:
        return None
    m = Micromap(data)
    return m if m.ok else None


# --------------------------------------------------------------------------- #
#  analysis positions
# --------------------------------------------------------------------------- #
# channel -> energy calibration measured on the reference project
# (INCA 20 keV / 2K-channel setting, refitted: E = 9.9885*ch - 197.7 eV,
#  rms residual 5 eV against 12 known lines)
EV_PER_CH = 9.9885
ZERO_CH = 19.80


def to_energy(ch, ev_per_ch=EV_PER_CH, zero_ch=ZERO_CH):
    return (ch - zero_ch) * ev_per_ch


class ChordList:
    """Decoded ``ChordList``: where INCA drew the analysis on the image.

       0x00 float   scale, always 1.0 in every project seen
       0x04 uint16  image width in pixels (512, 1024) -- a bound check
       0x06 uint32  point count
       0x0A         count * (uint16 row, uint16 column, uint16 kind)

    INCA writes (row, column), i.e. y first and x second; that was established
    on five projects, not guessed (the second value is the one bounded by the
    stored image width, and drawing x=column, y=row makes BSE brightness follow
    the analysed chemistry, r = 0.80 versus r = 0.13 for the swapped reading).
    Here the points are exposed as (x, y, kind) = (column, row, kind).

    An acquisition that never stored coordinates leaves a 10-byte stream with
    count = 0; that is reported as ``state = "empty"``, not as an error.
    """

    def __init__(self, data):
        self.scale = 1.0
        self.width = 0
        self.declared = 0
        self.points = []            # [(x, y, kind)]
        self.state = "none"
        self.note = ""
        self._parse(data or b"")

    def _parse(self, d):
        if len(d) < 10:
            return
        self.scale, = struct.unpack_from("<f", d, 0)
        self.width, = struct.unpack_from("<H", d, 4)
        self.declared, = struct.unpack_from("<I", d, 6)
        avail = (len(d) - 10) // 6
        count = self.declared
        if count > avail:
            self.note = ("счётчик точек %d больше, чем помещается в поток (%d) — "
                         "прочитаны первые %d" % (count, avail, avail))
            count = avail
        elif count == 0 and avail:
            # a writer that forgot the counter but stored the records
            self.note = ("счётчик точек 0, но в потоке есть %d записей — "
                         "прочитаны они" % avail)
            count = avail
        for i in range(count):
            row, col, kind = struct.unpack_from("<HHH", d, 10 + i * 6)
            self.points.append((col, row, kind))
        if self.points:
            self.state = "line" if len(self.points) > 1 else "point"
            # sanity check of the (row, column) convention: the width field is
            # the image width, so *columns* must respect it.  If instead every
            # row respects it, the pair was written the other way round.
            if self.width >= 64:
                xs = [p[0] for p in self.points]
                ys = [p[1] for p in self.points]
                if max(xs) > self.width and max(ys) <= self.width:
                    self.points = [(y, x, k) for (x, y, k) in self.points]
                    self.note = ((self.note + "; ").strip("; ") +
                                 "пара записана как (столбец, строка) — "
                                 "прочитана как (строка, столбец)")
        elif self.declared == 0 and len(d) <= 10:
            self.state = "empty"

    @property
    def ok(self):
        return bool(self.points)

    def __repr__(self):
        return "<ChordList %s %d pts>" % (self.state, len(self.points))


def parse_chord_list(data):
    """ChordList stream -> [(x, y, kind), ...] in image pixels (may be empty)."""
    return ChordList(data).points


# --------------------------------------------------------------------------- #
#  stream-role lookup
# --------------------------------------------------------------------------- #
# A role is a list of known relative paths ("parent/stream"); matching is
# case-insensitive and falls back to a suffix match, so an extra storage layer
# introduced by another INCA build does not hide the data.
ROLES = {
    "spectrum":        ("xraydata/spectrum", "spectrum"),
    "peak_labels":     ("peaklabels/peaklabels", "peaklabels"),
    "chord":           ("chordlist/chordlist", "chordlist"),
    "em_conditions":   ("emconditions/emconditions", "emconditions"),
    "elements":        ("elements",),
    "ed_detector":     ("xraydetector/ed_detector", "ed_detector"),
    "xray_detector":   ("xraydetector/xraydetector", "xraydetector"),
    "processor":       ("xraydetector/isis_processor", "xraydetector/xstreamprocessor",
                        "isis_processor", "xstreamprocessor"),
    "quant_calib":     ("quantcalibration/quantcalibration", "quantcalibration"),
    "information":     ("information",),
    "xgt2":            ("xgt2data/xgt2data", "xgt2data"),
    "strobe":          ("xraydata/strobe", "strobe"),
    "grey_image":      ("greyimage/micromap", "greyimage/image", "micromap"),
    "overlay_image":   ("overlaydata/micromap", "overlaydata/image", "overlaydata"),
    "image_label":     ("greyimage/label", "label"),
    "image_data":      ("imagedata",),
    "annotation":      ("annotation",),
}


def stream_named(streams, *names):
    """streams: {relative path: bytes} -> bytes of the first matching name.

    Matching is case-insensitive and tries the exact relative path first, then
    a path suffix, so an extra storage layer added by another INCA build does
    not hide the data.
    """
    if not streams or not names:
        return b""
    low = {k.lower(): k for k in streams}
    for n in names:
        key = n.lower()
        if key in low:
            return streams[low[key]]
    for n in names:
        key = n.lower()
        for k, orig in low.items():
            if k.endswith("/" + key):
                return streams[orig]
    return b""


def pick(streams, role):
    """streams: {relative path: bytes} -> the bytes for a role, or b\"\"."""
    if role not in ROLES:
        return b""
    return stream_named(streams, *ROLES[role])


# --------------------------------------------------------------------------- #
#  project model
# --------------------------------------------------------------------------- #
class Spectrum:
    """One EDS spectrum of a field of view (or the site's calibration one)."""

    def __init__(self, path, streams, role="analysis"):
        self.path = path
        self.streams = streams
        self.role = role

    @property
    def id(self):
        return self.path[-1]

    def stream(self, role):
        return pick(self.streams, role)

    def counts(self):
        c, _ = parse_spectrum(self.stream("spectrum"))
        return c

    def header(self):
        _, h = parse_spectrum(self.stream("spectrum"))
        return h

    def labels(self):
        return parse_peak_labels(self.stream("peak_labels"))

    def chord(self):
        return ChordList(self.stream("chord"))

    def points(self):
        """[(x, y)] of the analysis position on the FOV image, empty if absent"""
        return [(x, y) for (x, y, _kind) in self.chord().points]

    def elements(self):
        """element flag table -> tuple of uint32 flags"""
        d = self.stream("elements")
        return struct.unpack_from("<%dI" % (len(d) // 4), d, 0) if len(d) >= 4 else ()

    def element_set(self):
        """{Z} the acquisition searched for, or None when the table is absent.

        Quantifying only these elements is what INCA itself does; it also keeps
        a line that was never acquired out of the result (a Cr K-beta peak in a
        Cr-bearing sample otherwise shows up as "MnO").
        """
        d = self.stream("elements")
        tab = parse_elements_table(d)
        if not tab:
            return None
        return set(tab)

    def em_conditions(self):
        return parse_em_conditions(self.stream("em_conditions"))

    def ed_detector(self):
        return parse_ed_detector(self.stream("ed_detector"))

    def xray_detector(self):
        return parse_xray_detector(self.stream("xray_detector"))

    def family(self):
        """'isis' / 'xstream' / '' -- from the processor stream, then the family id"""
        if pick(self.streams, "processor"):
            low = "/".join(self.streams.keys()).lower()
            if "xstreamprocessor" in low:
                return "xstream"
            if "isis_processor" in low:
                return "isis"
        fam = self.xray_detector().get("family_id")
        return FAMILY_NAMES.get(fam, "")

    def total_counts(self):
        c = self.counts()
        return sum(c)


class Image:
    """One electron image (``Images/SEI...``) of a field of view."""

    def __init__(self, path, streams):
        self.path = path
        self.streams = streams

    @property
    def id(self):
        return self.path[-1]

    def stream(self, role):
        return pick(self.streams, role)

    def micromap(self):
        return Micromap(self.stream("grey_image"))

    def overlay(self):
        return Micromap(self.stream("overlay_image"))

    def label(self):
        return cp1251_runs(self.stream("image_label"), 2)

    def metadata(self):
        """ImageData is a 48-byte display/ROI record; only its version is
        unambiguous, so nothing else is guessed from it."""
        d = self.stream("image_data")
        out = {"version": None, "size": len(d)}
        if len(d) >= 4:
            out["version"], = struct.unpack_from("<I", d, 0)
        return out

    # backwards compatible accessor: (Micromap, raw bytes)
    def grey(self):
        m = self.micromap()
        return m, m.raw


class FOV:
    """One field of view = one analysis site (``FOVs/FOV...``)."""

    def __init__(self, path, streams, spectra, calib_spectra, images, wds=0):
        self.path = path
        self.streams = streams
        self.spectra = spectra
        self.calib_spectra = calib_spectra
        self.images = images
        self.wds_count = wds

    @property
    def id(self):
        return self.path[-1]

    def name(self):
        return read_named(stream_named(self.streams, "IFOVInfo", "FOVInfo"))

    def analyses(self):
        """[(index, spectrum, [(x, y), ...])] -- numbered point analyses of the
        site, index 1-based, with the position stored by INCA (may be empty)."""
        out = []
        for n, sp in enumerate(self.spectra, 1):
            out.append((n, sp, sp.points()))
        return out

    def has_positions(self):
        return any(pts for _n, _sp, pts in self.analyses())

    def chord_state(self):
        states = [sp.chord().state for sp in self.spectra]
        if "line" in states:
            return "line"
        if "point" in states:
            return "point"
        if "empty" in states:
            return "empty"
        return "none"


class Sample:
    """One sample (``Samples/SMP...``)."""

    def __init__(self, path, streams, fovs):
        self.path = path
        self.streams = streams
        self.fovs = fovs

    @property
    def id(self):
        return self.path[-1]

    def name(self):
        return read_named(stream_named(self.streams, "ISampleInfo", "SampleInfo"))


class ProjectFormat:
    """What kind of project this is -- detected, not assumed.

    Everything here is derived from the file itself, so the GUI/CLI and the
    calibration layer can adapt to an ISIS/Camebax project, an XStream/X-Max
    project, a spectra-only project without images or positions, or an
    instrument *installation* project.
    """

    def __init__(self, project):
        c = project.c
        self.container = c.version_label
        self.container_major = c.major
        self.container_version = (c.major, c.minor)
        self.n_samples = len(project.samples)
        fovs = list(project.iter_fovs())
        self.n_fovs = len(fovs)
        self.n_spectra = sum(len(f.spectra) for f in fovs)
        self.n_images = sum(len(f.images) for f in fovs)
        self.n_calib = sum(len(f.calib_spectra) for f in fovs)
        self.n_wds = sum(f.wds_count for f in fovs)

        families, serials, models, fam_ids = [], [], [], []
        bits = set()
        for f in fovs:
            for sp in f.spectra + f.calib_spectra:
                fam = sp.family()
                if fam:
                    families.append(fam)
                det = sp.ed_detector()
                if det.get("serial"):
                    serials.append(det["serial"])
                if det.get("model"):
                    models.append(det["model"])
                fid = sp.xray_detector().get("family_id")
                if fid is not None:
                    fam_ids.append(fid)
            for im in f.images:
                m = im.micromap()
                if m.ok:
                    bits.add(m.bits)

        self.family = self._pick(families)
        self.detector_serial = self._pick(serials)
        self.detector_model = self._pick(models)
        self.detector_family_id = self._pick([str(x) for x in fam_ids])
        self.image_bits = sorted(bits)

        states = [f.chord_state() for f in fovs]
        if "point" in states or "line" in states:
            self.chord_state = "points"
        elif "empty" in states:
            self.chord_state = "empty"
        else:
            self.chord_state = "none"
        self.n_points = sum(sum(len(sp.points()) for sp in f.spectra) for f in fovs)
        self.installation = project.is_installation()

    @staticmethod
    def _pick(values):
        """most common value, ties broken by first appearance"""
        if not values:
            return ""
        best, best_n = values[0], 0
        for v in values:
            n = values.count(v)
            if n > best_n:
                best, best_n = v, n
        return best

    # ---- human readable ----
    @property
    def family_label(self):
        if self.family == "isis":
            return "ISIS (Camebax)"
        if self.family == "xstream":
            return "XStream (XGT2)"
        return "неизвестный"

    @property
    def detector_label(self):
        parts = []
        if self.detector_model:
            parts.append(self.detector_model.replace(".ini", ""))
        if self.detector_serial:
            parts.append("S/N %s" % self.detector_serial)
        if self.detector_family_id:
            parts.append("family %s" % self.detector_family_id)
        return ", ".join(parts)

    @property
    def images_label(self):
        if not self.n_images:
            return "нет"
        return "%d (%s)" % (self.n_images,
                            ", ".join("%d бит" % b for b in self.image_bits) or "?")

    @property
    def coordinates_label(self):
        if self.chord_state == "points":
            return "есть (%d точек)" % self.n_points
        if self.chord_state == "empty":
            return "нет: ChordList записан пустым"
        return "нет: ChordList отсутствует"

    def describe(self):
        """short Russian lines describing the detected project"""
        return [
            "Контейнер  : %s" % self.container,
            "Семейство  : %s" % self.family_label,
            "Детектор   : %s" % (self.detector_label or "не указан"),
            "Содержимое : %d обр., %d уч., %d спектров, %d калибр."
            % (self.n_samples, self.n_fovs, self.n_spectra, self.n_calib),
            "Изображения: %s" % self.images_label,
            "Координаты : %s" % self.coordinates_label,
        ] + (["Тип файла  : установочный проект прибора (installation)"]
             if self.installation else [])

    def as_dict(self):
        return {
            "container": self.container,
            "family": self.family,
            "family_label": self.family_label,
            "detector_serial": self.detector_serial,
            "detector_model": self.detector_model,
            "detector_family_id": self.detector_family_id,
            "samples": self.n_samples,
            "fovs": self.n_fovs,
            "spectra": self.n_spectra,
            "calib_spectra": self.n_calib,
            "wds_spectra": self.n_wds,
            "images": self.n_images,
            "image_bits": self.image_bits,
            "coordinates": self.chord_state,
            "points": self.n_points,
            "installation": self.installation,
        }

    def line(self):
        """one-line summary for a status bar"""
        det = self.detector_label
        return "%s%s · изображений: %s · координаты: %s" % (
            self.family_label, (" / " + det) if det else "",
            self.images_label, self.coordinates_label)


class Project:
    """An INCA .ipj/.ipl project, read read-only."""

    def __init__(self, filename):
        self.filename = filename
        self.c = CFBF(filename)
        self._kids = {}
        for path, e in self.c.paths:
            self._kids.setdefault(path[:-1], []).append(e)
        self._build()
        self.format = ProjectFormat(self)

    # ---- tree helpers -----------------------------------------------------
    def _children(self, path, kind=None):
        out = [e for e in self._kids.get(path, ()) if kind is None or e.type == kind]
        return out

    def _find(self, path, names, kind=1, depth=3):
        """first child storage (case-insensitive) matching one of ``names``"""
        if depth <= 0:
            return None
        want = tuple(n.lower() for n in names)
        for e in self._children(path, kind):
            if e.name.lower() in want:
                return path + (e.name,)
        for e in self._children(path, 1):
            got = self._find(path + (e.name,), names, kind, depth - 1)
            if got:
                return got
        return None

    def _direct_streams(self, path):
        out = {}
        for e in self._children(path, 2):
            out[e.name] = self.c.stream_bytes(e)
        return out

    def _subtree_streams(self, path):
        out = {}
        stack = [path]
        while stack:
            p = stack.pop()
            for e in self._kids.get(p, ()):
                q = p + (e.name,)
                if e.type == 2:
                    out["/".join(q[len(path):])] = self.c.stream_bytes(e)
                elif e.type in (1, 5):
                    stack.append(q)
        return out

    def _looks_like_fov(self, path):
        names = {e.name.lower() for e in self._children(path)}
        return bool(names & {"images", "spectra", "ifovinfo", "calibspectrum",
                             "wdspectra"})

    # ---- build ------------------------------------------------------------
    def _build(self):
        root = ("Root Entry",)
        self.info = self.c.find("IIncaProjectInfo") or b""
        self.samples = []
        self._installation = self._looks_like_installation(root)

        samples_path = self._find(root, ("samples", "sample"), depth=2)
        if samples_path is not None:
            for e in self._children(samples_path, 1):
                self.samples.append(self._build_sample(samples_path + (e.name,)))
            if not self.samples and self._looks_like_fov(samples_path):
                # a "Samples" storage without SMP sub-storages: the sample
                # *is* the storage
                self.samples.append(self._build_sample(samples_path))

    def _looks_like_installation(self, root):
        """instrument installation projects (DetectorTest, SystemSetUpData ...)"""
        markers = {"detector information", "systemsetupdata", "system set up data",
                   "detectortest", "detectorinstall", "mposetupdata",
                   "extradata", "extra data", "trainingchecklist", "statusdata"}
        for e in self._children(root):
            if e.name.lower() in markers:
                return True
            for sub in self._children(root + (e.name,)):
                if sub.name.lower() in markers:
                    return True
        return False

    def _build_sample(self, path):
        streams = self._direct_streams(path)
        fovs = []
        fovs_path = self._find(path, ("fovs",), depth=2)
        if fovs_path is not None:
            for e in self._children(fovs_path, 1):
                fovs.append(self._build_fov(fovs_path + (e.name,)))
        else:
            # some builds keep FOV storages directly under the sample
            subs = [e for e in self._children(path, 1) if self._looks_like_fov(
                path + (e.name,))]
            if subs:
                for e in subs:
                    fovs.append(self._build_fov(path + (e.name,)))
            elif self._looks_like_fov(path):
                fovs.append(self._build_fov(path))
        return Sample(path, streams, fovs)

    def _build_fov(self, path):
        streams = self._direct_streams(path)
        spectra, calib, images = [], [], []
        wds = 0

        sp_path = self._find(path, ("spectra", "spectrum"), depth=2)
        if sp_path is not None:
            subs = self._children(sp_path, 1)
            if subs:
                for e in subs:
                    p = sp_path + (e.name,)
                    spectra.append(Spectrum(p, self._subtree_streams(p)))
            else:
                d = self._subtree_streams(sp_path)
                if pick(d, "spectrum"):
                    spectra.append(Spectrum(sp_path, d))

        cp = self._find(path, ("calibspectrum",), depth=2)
        if cp is not None:
            calib.append(Spectrum(cp, self._subtree_streams(cp), role="calib"))

        im_path = self._find(path, ("images", "image"), depth=2)
        if im_path is not None:
            subs = self._children(im_path, 1)
            for e in subs:
                p = im_path + (e.name,)
                images.append(Image(p, self._subtree_streams(p)))
            if not subs:
                d = self._subtree_streams(im_path)
                if pick(d, "grey_image"):
                    images.append(Image(im_path, d))
        if not images and pick(streams, "grey_image"):
            images.append(Image(path, streams))

        wp = self._find(path, ("wdspectra", "wds"), depth=2)
        if wp is not None:
            wds = sum(1 for e in self._children(wp, 2))

        return FOV(path, streams, spectra, calib, images, wds)

    # ---- convenience ------------------------------------------------------
    def is_installation(self):
        return self._installation

    def iter_fovs(self):
        for s in self.samples:
            for f in s.fovs:
                yield f

    def all_spectra(self):
        for f in self.iter_fovs():
            for sp in f.spectra:
                yield f, sp

    def stats(self):
        n_spec = sum(len(f.spectra) for f in self.iter_fovs())
        n_img = sum(len(f.images) for f in self.iter_fovs())
        return {"samples": len(self.samples),
                "fovs": sum(1 for _ in self.iter_fovs()),
                "spectra": n_spec, "images": n_img}

    # ---- reporting --------------------------------------------------------
    def summary(self):
        print("file            : %s (%.1f MB)" %
              (os.path.basename(self.filename),
               os.path.getsize(self.filename) / 1e6))
        for line in self.format.describe():
            print("  " + line)
        print("project strings : %s" % ", ".join(cp1251_runs(self.info, 2)))
        for s in self.samples:
            print("sample          : %s  (%s)" % (s.name(), s.id))
            print("FOVs            : %d" % len(s.fovs))
            for f in s.fovs:
                print("  %-14s %-22s  images=%d  spectra=%d  points=%d"
                      % (f.id, f.name(), len(f.images), len(f.spectra),
                         sum(len(p) for _n, _sp, p in f.analyses())))
                for im in f.images:
                    m = im.micromap()
                    if m.ok:
                        print("      image %s  %dx%d  %d bit  %s"
                              % (im.id, m.width, m.height, m.bits, im.label()))
                for n, sp, pts in f.analyses():
                    c = sp.counts()
                    em = sp.em_conditions()
                    print("      spec  %-14s  %d ch  sum=%-8d  max=%-6d  %-6s  %s"
                          % (sp.id, len(c), sum(c), max(c) if c else 0,
                             ("%.1f kV" % em["kV"]) if em.get("kV") else "",
                             ("(%d pts)" % len(pts)) if len(pts) > 1
                             else (("(%d,%d)" % pts[0]) if pts else "(no pos)")))


def open_project(path):
    """Project(path) with a friendlier error for non-INCA files."""
    try:
        return Project(path)
    except Exception as e:
        raise ValueError("%s: %s" % (os.path.basename(path), e))


# --------------------------------------------------------------------------- #
#  export (legacy helper, kept for the module CLI)
# --------------------------------------------------------------------------- #
def export(project, outdir):
    imgdir = os.path.join(outdir, "images")
    specdir = os.path.join(outdir, "spectra")
    os.makedirs(imgdir, exist_ok=True)
    os.makedirs(specdir, exist_ok=True)

    rows = []
    for s in project.samples:
        for f in s.fovs:
            fname = re.sub(r"[^\w.]+", "_", f.name() or f.id)
            for im in f.images:
                m = im.micromap()
                if not m.ok:
                    continue
                pil = m.to_pil()
                out = os.path.join(imgdir, "%s_%s.png" % (fname, im.id))
                pil.save(out)
                ov = im.overlay()
                if ov.ok and (ov.width, ov.height) == (m.width, m.height):
                    ov.to_pil().save(os.path.join(
                        imgdir, "%s_%s_overlay.png" % (fname, im.id)))
                rows.append([s.name(), f.name(), im.id, m.width, m.height, out])

            for sp in f.spectra:
                c = sp.counts()
                if not c:
                    continue
                out = os.path.join(specdir, "%s_%s.csv" % (fname, sp.id))
                with open(out, "w", encoding="utf-8") as fh:
                    fh.write("channel,energy_eV,counts\n")
                    for i, v in enumerate(c):
                        fh.write("%d,%.1f,%d\n" % (i, to_energy(i), v))
                rows.append([s.name(), f.name(), sp.id, "spectrum", len(c), out,
                             sum(c), max(c)])

    with open(os.path.join(outdir, "index.csv"), "w", encoding="utf-8-sig") as fh:
        fh.write("sample;fov;object;a;b;file;total;max\n")
        for r in rows:
            fh.write(";".join(str(x).replace(";", ",") for x in r) + "\n")
    return rows


# --------------------------------------------------------------------------- #
def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    cmd, path = argv[1], argv[2]
    p = Project(path)
    if cmd == "list":
        p.summary()
    elif cmd == "export":
        out = argv[3] if len(argv) > 3 else "out"
        rows = export(p, out)
        print("wrote %d objects to %s" % (len(rows), out))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
