# -*- coding: utf-8 -*-
"""
pipeline.py -- one place that knows how to turn a project into files.

Everything that depends on the *file variant* is asked from the reader
(``ProjectFormat``); everything that depends on the *instrument* is asked from
the calibration profile.  This module only orchestrates:

    read project  ->  [quant with the profile]  ->  images + points + csv
"""
import os, sys, csv

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import inca_ipj as I
import analysis
import points as ptmod
import render
import quant as Q


def _legend_for(recs, has_points):
    rows = []
    for r in recs:
        if r.get("failed"):
            rows.append((str(r["index"]), "неудачная съёмка"))
            continue
        ox = r.get("oxides") or {}
        if not ox:
            rows.append((str(r["index"]), "нет оксидов (элементы: %s)"
                         % ", ".join(r.get("elements") or [])))
            continue
        top = sorted(((ox.get(k, 0), k) for k in analysis.OXIDE_ORDER), reverse=True)
        txt = ", ".join("%s %.1f" % (k, v * 100) for v, k in top[:4] if v * 100 >= 1.0)
        rows.append((str(r["index"]), txt))
    return rows


def positions_for_fov(f, sidecar=None):
    """[(label, spectrum_id, [(x, y), ...]), ...] and the source used.

    Coordinates stored by INCA win.  The sidecar is a manual override and is
    only consulted when the project itself has no position for that site.
    """
    stored = ptmod.from_project(f)
    if any(pts for _i, _sid, pts in stored):
        return stored, "project"
    hand = ptmod.for_fov(sidecar, f)
    if hand:
        return ([(lbl or str(i + 1), None, [(float(x), float(y))])
                 for i, (x, y, lbl) in enumerate(hand)], "sidecar")
    return stored, "none"


def resolve_profile(fmt, profile=None):
    """(Profile, reason) -- explicit choice, else the one matching the detector."""
    if isinstance(profile, Q.Profile):
        return profile, "выбран вручную"
    if isinstance(profile, str) and profile.strip():
        p = Q.profile(profile.strip())
        if p is not None:
            return p, "выбран вручную"
    return Q.suggest_for(fmt)


def export_project(project_path, outdir, app_dir=None, log=print,
                   do_quant=True, progress=None, profile=None):
    """Write images, points.csv and quant.csv for one project.

    profile: calibration profile slug or Profile; None = the one that matches
    the detected detector (see quant.suggest_for).
    """
    os.makedirs(outdir, exist_ok=True)
    imgdir = os.path.join(outdir, "images")
    os.makedirs(imgdir, exist_ok=True)

    log("Чтение проекта: %s" % os.path.basename(project_path))
    proj = I.Project(project_path)
    fmt = proj.format
    for line in fmt.describe():
        log("  " + line)

    prof, reason = resolve_profile(fmt, profile)
    recs = []
    if do_quant:
        if not prof.has_standards():
            log("Калибровка: в профиле «%s» нет %s — расчёт пропущен"
                % (prof.title, Q.STANDARDS_CSV))
        else:
            log("Калибровка: %s (%s; %s)" % (prof.title, prof.slug, reason))
            try:
                recs = analysis.analyse_project(project=proj, cal_dir=prof.path,
                                                progress=progress)
            except Exception as e:
                log("Количественный расчёт недоступен: %s" % e)
                recs = []
    by_fov = {}
    for r in recs:
        by_fov.setdefault((r["fov_id"], r["site"]), []).append(r)

    sidecar = ptmod.load(project_path, app_dir)
    if sidecar:
        log("Файл координат: %s (используется, только если в проекте их нет)"
            % ptmod.find_sidecar(project_path, app_dir))

    n_img = 0
    n_cards = 0
    n_pts = 0
    n_sites_with_coords = 0
    n_sites_without_image = 0
    coord_rows = []
    for s in proj.samples:
        for f in s.fovs:
            fname = render_safe(f.name() or f.id)
            positions, source = positions_for_fov(f, sidecar)
            if source != "none":
                n_sites_with_coords += 1
            fov_recs = by_fov.get((f.id, f.name()), [])
            rows = _legend_for(fov_recs, bool(positions))

            # positions belong to the site, not to a particular image
            pts, lines = [], []
            for (label, sid, xy) in positions:
                if len(xy) > 1:
                    lines.append((label, xy))
                elif xy:
                    pts.append((xy[0][0], xy[0][1], label))
                n_pts += len(xy)
                for k, (x, y) in enumerate(xy, 1):
                    coord_rows.append([s.name(), f.name(), f.id, sid or "",
                                       label, k, len(xy),
                                       "line" if len(xy) > 1 else "point",
                                       "%.1f" % x, "%.1f" % y])

            note = None
            if source == "none":
                note = ("координаты точек в проекте не сохранены — "
                        "анализы перечислены в легенде")
            elif source == "sidecar":
                note = "координаты точек взяты из файла-спутника"

            if not f.images:
                n_sites_without_image += 1
                if rows:
                    card = os.path.join(imgdir, "%s_%s_analyses.png" % (fname, f.id))
                    render.legend_card(
                        rows, card,
                        title="%s — %s" % (s.name(), f.name() or f.id),
                        note=note or ("растра в проекте нет: только таблица анализов"))
                    n_cards += 1

            for im in f.images:
                m = im.micromap()
                if not m.ok:
                    log("  изображение %s пропущено: поток не читается" % im.id)
                    continue
                grey = m.to_pil()
                base = os.path.join(imgdir, "%s_%s" % (fname, im.id))
                render.save_plain(grey, base + ".png")
                render.annotate(grey, (m.width, m.height), base + "_annotated.png",
                                title="%s — %s" % (s.name(), f.name()),
                                points=pts, lines=lines, legend=rows, note=note,
                                field_width_um=im.field_width_um())
                n_img += 1
                log("  изображение %s  %dx%d %d бит  точек=%d  линий=%d"
                    % (im.id, m.width, m.height, m.bits, len(pts), len(lines)))

    if coord_rows:
        csv_path = os.path.join(outdir, "points.csv")
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
            wr = csv.writer(fh, delimiter=";")
            wr.writerow(["sample", "site", "fov_id", "spectrum", "label",
                         "point", "n_points", "type", "x", "y"])
            wr.writerows(coord_rows)
        log("Координаты точек: %s (%d точек на %d участках)"
            % (csv_path, n_pts, n_sites_with_coords))
    else:
        log("Координаты точек в проекте не найдены — вынесена только легенда.")

    if recs:
        csv_path = os.path.join(outdir, "quant.csv")
        analysis.write_csv(recs, csv_path)
        ok = sum(1 for r in recs if not r.get("failed") and not r.get("no_oxides"))
        log("Таблица результатов: %s (%d строк, из них %d с оксидами)"
            % (csv_path, len(recs), ok))
    log("Готово: %d изображений в %s" % (n_img, imgdir))
    if n_cards:
        log("Карточек с таблицей анализов (участки без растра): %d" % n_cards)
    if n_sites_without_image:
        log("Участков без изображения: %d (в проекте нет растра)" % n_sites_without_image)
    return {"images": n_img, "cards": n_cards, "records": recs, "outdir": outdir,
            "points": n_pts, "sites_with_coords": n_sites_with_coords,
            "profile": prof.slug, "profile_title": prof.title,
            "reason": reason, "format": fmt.as_dict()}


def render_safe(name):
    import re
    return re.sub(r"[^\w.\-]+", "_", name).strip("_") or "fov"


def app_dir():
    """folder of the program (the .exe folder when frozen) -- sidecars live there"""
    return Q.app_dir()
