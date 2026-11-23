# -*- coding: utf-8 -*-
"""
render.py -- turn an INCA greyscale micromap into report-ready images.

Two products per field of view:
  * <fov>.png            the image exactly as acquired
  * <fov>_annotated.png  the image with the analysed areas, their numbers and a
                         side legend

Where do the areas come from
    INCA stores them in ``Spectra/SPE.../ChordList`` (see inca_ipj.ChordList):
    one record for a point analysis, several consecutive records for a rastered
    line.  A sidecar file (points.py) is used only when the project has none.

How they are drawn
    INCA marks an analysed area as a rectangle, so that is what is drawn here:
    the bounding box of the stored chord, with a minimum size so a single point
    stays visible.  The chord itself is drawn faintly inside the rectangle so
    the raster path remains readable.
"""
import os
from PIL import Image as PILImage, ImageDraw, ImageFont

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\tahoma.ttf",
    r"C:\Windows\Fonts\dejavusans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]

MARKER_COLOURS = [(255, 40, 40), (30, 160, 255), (40, 200, 90),
                  (255, 170, 0), (200, 60, 220), (0, 210, 210)]

MIN_BOX = 11          # px, smallest drawn area (a single-point analysis)


def _font(size, bold=False):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                f = ImageFont.truetype(p, size)
                if bold:
                    try:
                        f.set_variation_by_axes([700])
                    except Exception:
                        pass
                return f
            except Exception:
                continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _text_size(draw, text, font):
    try:
        b = draw.textbbox((0, 0), text, font=font)
        return b[2] - b[0], b[3] - b[1]
    except Exception:
        return (len(text) * 6, 12)


def _label_box(draw, ax, ay, label, font, colour):
    """draw a number on a dark plate at (ax, ay) = left-middle anchor"""
    tw, th = _text_size(draw, label, font)
    pad = 3
    bx0 = ax + 4
    by0 = ay - th // 2 - pad
    draw.rectangle([bx0, by0, bx0 + tw + 2 * pad, by0 + th + 2 * pad], fill=(0, 0, 0))
    draw.text((bx0 + pad, by0 + pad - 1), label, font=font, fill=colour)


def draw_point_area(draw, x, y, label, font, w=0, h=0, colour=(255, 40, 40)):
    """A point analysis: a small square (INCA's usual rectangle marker)."""
    w = w if w and w > 1 else MIN_BOX
    h = h if h and h > 1 else MIN_BOX
    x0, y0 = x - w / 2.0, y - h / 2.0
    draw.rectangle([x0, y0, x0 + w, y0 + h], outline=colour, width=2)
    draw.line([(x - 3, y), (x + 3, y)], fill=colour, width=1)
    draw.line([(x, y - 3), (x, y + 3)], fill=colour, width=1)
    _label_box(draw, x0 + w, y, label, font, colour)


def draw_line_area(draw, pts, label, font, colour=(255, 40, 40), min_box=MIN_BOX):
    """A rastered analysis: rectangle around the chord plus the chord itself."""
    if not pts:
        return
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    if x1 - x0 < min_box:
        c = (x0 + x1) / 2.0
        x0, x1 = c - min_box / 2.0, c + min_box / 2.0
    if y1 - y0 < min_box:
        c = (y0 + y1) / 2.0
        y0, y1 = c - min_box / 2.0, c + min_box / 2.0
    draw.rectangle([x0 - 1, y0 - 1, x1 + 1, y1 + 1], outline=colour, width=2)
    if len(pts) > 1:
        draw.line([(x, y) for (x, y) in pts], fill=colour, width=1)
    _label_box(draw, x1 + 1, (y0 + y1) / 2.0, label, font, colour)


def annotate(grey, size, out_path, title="", points=None, lines=None, legend=None,
             note=None, scale=1):
    """grey: PIL 'L' image.

    points: [(x, y, label)] or [(x, y, w, h, label)] in image pixel coordinates
    lines:  [(label, [(x, y), ...])] rastered analyses
    legend: list of (label, text) rows drawn in a strip on the right
    """
    img = grey.convert("RGB")
    if scale != 1:
        img = img.resize((int(img.width * scale), int(img.height * scale)),
                         PILImage.LANCZOS)
    f_mark = _font(max(12, int(13 * scale)), bold=True)
    f_small = _font(max(11, int(12 * scale)))
    f_title = _font(max(13, int(15 * scale)), bold=True)

    rows = legend or []
    strip_w = 0
    if rows:
        probe = ImageDraw.Draw(img)
        widest = 0
        for lbl, txt in rows:
            tw, _ = _text_size(probe, "%s  %s" % (lbl, txt), f_small)
            widest = max(widest, tw)
        strip_w = min(max(widest + 34, 300), 460)
    head_h = int(30 * scale) if title else 0
    note_h = int(22 * scale) if note else 0
    total_w = img.width + strip_w
    total_h = img.height + head_h + note_h
    canvas = PILImage.new("RGB", (total_w, total_h), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    if title:
        draw.text((8, 6), title, font=f_title, fill=(0, 0, 0))
    draw.rectangle([0, head_h, img.width - 1, head_h + img.height - 1],
                   outline=(120, 120, 120))
    canvas.paste(img, (0, head_h))

    for (label, pts) in (lines or []):
        scaled = [(x * scale, y * scale + head_h) for (x, y) in pts]
        draw_line_area(draw, scaled, str(label), f_mark,
                       min_box=max(MIN_BOX, MIN_BOX * scale))

    for p in (points or []):
        x, y = p[0], p[1]
        if len(p) >= 5:
            w, h, label = p[2], p[3], p[4]
        elif len(p) == 3:
            w, h, label = 0, 0, p[2]
        else:
            w, h, label = 0, 0, ""
        px, py = x * scale, y * scale + head_h
        if not (0 <= px < img.width and 0 <= py < img.height):
            continue
        draw_point_area(draw, px, py, str(label), f_mark,
                        w * scale if w else 0, h * scale if h else 0)

    if rows:
        x0 = img.width + 12
        y = head_h + 8
        draw.text((x0, y), "Точки анализа", font=f_title, fill=(0, 0, 0))
        y += int(24 * scale)
        for lbl, txt in rows:
            if y > total_h - note_h - 6:
                break
            draw.text((x0, y), str(lbl), font=f_small, fill=(190, 20, 20))
            draw.text((x0 + int(34 * scale), y), str(txt), font=f_small,
                      fill=(20, 20, 20))
            y += max(int(15 * scale), _text_size(draw, txt, f_small)[1] + int(6 * scale))

    if note:
        draw.text((8, total_h - note_h + 3), note, font=f_small, fill=(150, 60, 0))

    canvas.save(out_path)
    return out_path


def save_plain(grey, out_path):
    grey.save(out_path)
    return out_path


def legend_card(rows, out_path, title="", note=None, col_w=430, max_per_col=44):
    """A text-only card with the analyses of a site.

    Some projects (an INCA site saved without a stored raster, a spectra-only
    acquisition) have nothing to draw the points on.  Instead of leaving the
    site without any output, the same legend that would go beside an image is
    written as a picture of its own.
    """
    f_title = _font(15, bold=True)
    f_row = _font(12)
    f_note = _font(11)
    probe = ImageDraw.Draw(PILImage.new("RGB", (10, 10)))
    line_h = max(16, _text_size(probe, "Ag", f_row)[1] + 4)
    n = max(1, len(rows))
    ncol = max(1, (n + max_per_col - 1) // max_per_col)
    per_col = max(1, (n + ncol - 1) // ncol)
    w = col_w * ncol + 20
    h = 56 + min(per_col, n) * line_h + (28 if note else 0) + 8
    canvas = PILImage.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    if title:
        draw.text((10, 8), title, font=f_title, fill=(0, 0, 0))
    draw.text((10, 30), "Точки анализа", font=f_title, fill=(0, 0, 0))
    y0 = 56
    for i, (lbl, txt) in enumerate(rows):
        col, row = divmod(i, per_col)
        x = 10 + col * col_w
        y = y0 + row * line_h
        draw.text((x, y), str(lbl), font=f_row, fill=(190, 20, 20))
        draw.text((x + 38, y), str(txt), font=f_row, fill=(20, 20, 20))
    if note:
        draw.text((10, h - 24), note, font=f_note, fill=(150, 60, 0))
    canvas.save(out_path)
    return out_path
