# -*- coding: utf-8 -*-
"""
selftest.py -- read every project you point it at and report what was found.

    newINCA.exe selftest <file.ipj> [more files or folders ...]

The point is not a unit test of the code but a *format* test: on a machine with
projects from several instruments this prints, for each file, which dialect was
detected, how many objects were read and which ones look wrong (an image whose
raster does not match its header, a spectrum without counts, a coordinate
outside the image, ...).  Exit code 1 if anything hard failed.
"""
import os, sys, glob

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import inca_ipj as I


def find_projects(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            for ext in ("*.ipj", "*.ipl", "*.IPJ", "*.IPL"):
                out.extend(glob.glob(os.path.join(p, "**", ext), recursive=True))
        elif os.path.isfile(p):
            out.append(p)
    seen, uniq = set(), []
    for p in out:
        k = os.path.abspath(p).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return sorted(uniq)


def check_project(path):
    """-> (project|None, problems[list], warnings[list])"""
    problems, warnings = [], []
    try:
        proj = I.Project(path)
    except Exception as e:
        return None, ["не читается: %s" % e], warnings

    fmt = proj.format
    for f in proj.iter_fovs():
        for sp in f.spectra + f.calib_spectra:
            c = sp.counts()
            if not c:
                problems.append("%s/%s: нет данных спектра" % (f.id, sp.id))
            n = len(f.images)
            pts = sp.points()
            if pts and n:
                m = f.images[0].micromap()
                if m.ok:
                    bad = [p for p in pts if not (0 <= p[0] < m.width
                                                  and 0 <= p[1] < m.height)]
                    if bad:
                        warnings.append("%s/%s: %d точек вне кадра %dx%d"
                                        % (f.id, sp.id, len(bad), m.width, m.height))
            if sp.points() and not pts:
                warnings.append("%s/%s: координаты не читаются" % (f.id, sp.id))
        for im in f.images:
            m = im.micromap()
            if not m.ok:
                problems.append("%s/%s: изображение не читается" % (f.id, im.id))
                continue
            if not m.exact:
                warnings.append("%s/%s: длина потока не совпадает с заголовком "
                                "(кадр %dx%d, %d бит)"
                                % (f.id, im.id, m.width, m.height, m.bits))
            if m.bits not in (8, 16):
                warnings.append("%s/%s: %d бит на пиксель" % (f.id, im.id, m.bits))
    return proj, problems, warnings


def run(paths):
    files = find_projects(paths)
    if not files:
        print("не найдено ни одного .ipj / .ipl")
        return 1
    hard = 0
    print("%-42s %-9s %4s %4s %5s %5s %6s  %s"
          % ("файл", "семейство", "обр", "уч", "спект", "изобр", "точки", "калибровка"))
    for p in files:
        proj, problems, warnings = check_project(p)
        if proj is None:
            hard += 1
            print("%-42s %s" % (os.path.basename(p)[:42], problems[0]))
            continue
        fmt = proj.format
        try:
            import quant as Q
            import pipeline
            prof, _reason = pipeline.resolve_profile(fmt)
            prof_name = prof.slug if prof.has_standards() else "нет данных"
        except Exception as e:
            prof_name = "ошибка: %s" % e
        print("%-42s %-9s %4d %4d %5d %5d %6d  %s"
              % (os.path.basename(p)[:42], fmt.family or "-",
                 fmt.n_samples, fmt.n_fovs, fmt.n_spectra, fmt.n_images,
                 fmt.n_points, prof_name))
        for w in warnings[:6]:
            print("    ! %s" % w)
        for e in problems[:6]:
            print("    x %s" % e)
        if problems:
            hard += 1
    print("\nфайлов: %d, с ошибками: %d" % (len(files), hard))
    return 1 if hard else 0
