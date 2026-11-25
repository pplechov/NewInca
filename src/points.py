# -*- coding: utf-8 -*-
"""
points.py -- where the analysed areas are on the image.

Primary source: the project itself.  INCA stores the drawn outline of each
analysis in ``Spectra/SPE.../ChordList`` -- one record for a point analysis,
several consecutive records for a rastered line.  See
``inca_ipj.ChordList``; ``from_project()`` turns that into a plain list.

Fallback: a sidecar file next to the project, used only when the project has no
coordinates for a site (an acquisition made without drawing on the image), or
to correct them by hand.  Keeping it outside the project means the .ipj stays
byte-identical to what INCA wrote.

Sidecar format (semicolon separated, UTF-8, optional BOM):

    fov;x;y;label
    FOVBC3A47CB6BB;312;145;1
    Участок анализа 3;640;330;2

* fov   - the FOV storage id, or the FOV name
* x, y  - pixel coordinates on the image
* label - caption; empty means the running analysis number

Lines starting with '#' and blank lines are ignored.
"""
import os, csv


def sidecar_paths(project_path, app_dir=None):
    base = os.path.splitext(os.path.basename(project_path))[0]
    d = os.path.dirname(os.path.abspath(project_path))
    paths = [os.path.join(d, base + ".points.csv")]
    if app_dir:
        paths.append(os.path.join(app_dir, "points", base + ".csv"))
    return paths


def find_sidecar(project_path, app_dir=None):
    for p in sidecar_paths(project_path, app_dir):
        if os.path.exists(p):
            return p
    return None


def load(project_path, app_dir=None):
    """returns {fov_key: [(x, y, label), ...]}"""
    path = find_sidecar(project_path, app_dir)
    out = {}
    if not path:
        return out
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.reader(fh, delimiter=";"):
            if not row or row[0].strip().startswith("#"):
                continue
            if len(row) < 3 or row[0].strip().lower() == "fov":
                continue
            try:
                x, y = float(row[1]), float(row[2])
            except ValueError:
                continue
            label = row[3].strip() if len(row) > 3 else ""
            out.setdefault(row[0].strip(), []).append((x, y, label))
    return out


def save(project_path, data, app_dir=None):
    """data: {fov_key: [(x, y, label), ...]}"""
    path = sidecar_paths(project_path, app_dir)[0]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["fov", "x", "y", "label"])
        for key, pts in data.items():
            for (x, y, label) in pts:
                w.writerow([key, "%.1f" % x, "%.1f" % y, label])
    return path


def for_fov(data, fov):
    """look up by storage id first, then by display name"""
    if not data:
        return []
    if fov.id in data:
        return data[fov.id]
    name = fov.name()
    if name and name in data:
        return data[name]
    return []


def from_project(fov):
    """[(label, spectrum_id, [(x, y), ...])] stored by INCA for this site.

    A point analysis yields one coordinate, a rastered one yields the whole
    chord (a vertical run of rows); the pipeline draws the first as a circle
    and the second as the rectangle bounding the chord.
    """
    out = []
    try:
        analyses = fov.analyses()
    except Exception:
        return out
    for (n, sp, pts) in analyses:
        out.append((str(n), sp.id,
                    [(float(x), float(y)) for (x, y) in pts]))
    return out
