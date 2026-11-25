# -*- coding: utf-8 -*-
"""app.py -- the graphical interface of newINCA (one Tk window per platform)."""
import os, sys, threading, traceback, queue, configparser, datetime, math, json

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import inca_ipj as I
import points as ptmod
import pipeline
import render
import analysis
import quant as Q

APP_DIR = Q.app_dir()          # writable data folder: settings, sidecars, out
APP_TITLE = "newINCA — просмотр и экспорт проектов INCA"
SUPPORTED = [("Проекты INCA", "*.ipj *.ipl *.IPJ *.IPL"), ("Все файлы", "*.*")]

# Tk substitutes a default when the family is missing, so one name per platform
# is enough; the label size stays the same on every OS.
UI_FONT = {"win32": "Segoe UI", "darwin": "Helvetica Neue"}.get(sys.platform,
                                                               "DejaVu Sans")

SPEC_BG = "#101418"
AXIS = "#8899aa"


# --------------------------------------------------------------------------- #
#  UI settings: the last folders are remembered between runs
# --------------------------------------------------------------------------- #
def _ui_path():
    d = APP_DIR
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.path.dirname(os.path.abspath(__file__)) or "."
    return os.path.join(d, "ui.ini")


def ui_load():
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(_ui_path(), encoding="utf-8")
    except Exception:
        pass
    return cp


def ui_save(cp):
    try:
        with open(_ui_path(), "w", encoding="utf-8") as fh:
            cp.write(fh)
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.minsize(1000, 660)

        self._ui = ui_load()
        geo = self._ui.get("ui", "geometry", fallback="1280x820")
        try:
            self.geometry(geo)
        except Exception:
            self.geometry("1280x820")

        self.project_path = None
        self.proj = None
        self.records = []
        self.sidecar = {}
        self.outdir = self._ui.get("ui", "out_dir", fallback=os.path.join(APP_DIR, "out"))
        self.last_dir = self._ui.get("ui", "last_dir", fallback="")
        try:
            recent = json.loads(self._ui.get("ui", "recent_projects", fallback="[]"))
        except ValueError:
            recent = []
        self.recent_projects = ([path for path in recent if isinstance(path, str) and path][:10]
                                if isinstance(recent, list) else [])
        self.current_fov = None
        self.placed = []
        self.lines = []
        self._img_ref = None
        self._image_source = None
        self._image_status = ""
        self._field_width_um = None
        self._scale = 1.0
        self._profiles = []
        self._mac_sets = []
        self._logq = queue.Queue()
        self._std = None
        self._std_key = None
        self._export_finished = False
        # spectrum view: (energy_lo, energy_hi) in keV, None = whole spectrum
        self._view = None
        self._spec_range = None
        self._drag = None

        self._build_ui()
        self._refresh_profiles()
        self._refresh_mac_sets()
        self.after(120, self._drain_log)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.log("Готово. Откройте файл .ipj (или .ipl) через «Открыть проект…».")
        self.log("Калибровка: %s" % Q.active_profile().title)
        self.log("Коэффициенты поглощения: %s" % Q.mac_title())

    # ------------------------------------------------------------------ ui
    def _build_ui(self):
        bar = ttk.Frame(self, padding=(6, 4))
        bar.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(bar, text="Открыть проект…", command=self.open_project).pack(side=tk.LEFT)
        recent_button = ttk.Menubutton(bar, text="▾", width=2)
        self.recent_menu = tk.Menu(recent_button, tearoff=False,
                                   postcommand=self._populate_recent_menu)
        recent_button.configure(menu=self.recent_menu)
        recent_button.pack(side=tk.LEFT)
        ttk.Button(bar, text="Папка вывода…", command=self.choose_out).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)
        self.btn_export = ttk.Button(bar, text="Экспорт изображений и расчёт",
                                     command=self.export_all, state=tk.DISABLED)
        self.btn_export.pack(side=tk.LEFT)

        ttk.Button(bar, text="?", width=3, command=self.show_calibration).pack(side=tk.RIGHT)
        self.profile_box = ttk.Combobox(bar, state="readonly", width=30)
        self.profile_box.pack(side=tk.RIGHT)
        self.profile_box.bind("<<ComboboxSelected>>", self.on_profile_change)
        ttk.Label(bar, text="Калибровка:").pack(side=tk.RIGHT, padx=(12, 4))

        self.absorb_var = tk.BooleanVar(value=Q.absorption_enabled())
        ttk.Checkbutton(bar, text="поправка", variable=self.absorb_var,
                        command=self.on_absorption_change).pack(side=tk.RIGHT, padx=(0, 4))
        self.mac_box = ttk.Combobox(bar, state="readonly", width=22)
        self.mac_box.pack(side=tk.RIGHT)
        self.mac_box.bind("<<ComboboxSelected>>", self.on_mac_change)
        ttk.Label(bar, text="μ/ρ:").pack(side=tk.RIGHT, padx=(12, 4))

        pw = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        pw.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        left = ttk.Frame(pw, width=330)
        pw.add(left, weight=0)
        ttk.Label(left, text="Содержимое проекта").pack(anchor="w")
        self.tree = ttk.Treeview(left, selectmode="browse")
        self.tree.pack(fill=tk.BOTH, expand=True, pady=(2, 2))
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        right = ttk.Frame(pw)
        pw.add(right, weight=1)

        vp = ttk.PanedWindow(right, orient=tk.VERTICAL)
        vp.pack(fill=tk.BOTH, expand=True)

        # --- image ------------------------------------------------------
        top = ttk.Frame(vp)
        vp.add(top, weight=3)
        self.canvas = tk.Canvas(top, background="#2b2b2b", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", self._resize_image)

        # --- analyses + spectrum + info ---------------------------------
        bottom = ttk.Frame(vp)
        vp.add(bottom, weight=3)
        hp = ttk.PanedWindow(bottom, orient=tk.HORIZONTAL)
        hp.pack(fill=tk.BOTH, expand=True)

        lf = ttk.Frame(hp, width=230)
        hp.add(lf, weight=0)
        ttk.Label(lf, text="Анализы участка").pack(anchor="w")
        self.lst = tk.Listbox(lf, exportselection=False, width=30)
        self.lst.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
        self.lst.bind("<<ListboxSelect>>", self.on_analysis_select)

        rf = ttk.Frame(hp)
        hp.add(rf, weight=1)
        spectrum_controls = ttk.Frame(rf)
        spectrum_controls.pack(fill=tk.X)
        ttk.Label(spectrum_controls, text="Шкала интенсивности").pack(side=tk.LEFT)
        self.spectrum_scale = ttk.Combobox(
            spectrum_controls, values=("Логарифмическая", "Линейная"),
            state="readonly", width=19)
        self.spectrum_scale.current(0)
        self.spectrum_scale.pack(side=tk.LEFT, padx=6)
        self.spectrum_scale.bind("<<ComboboxSelected>>", lambda e: self._redraw_spectrum())
        peak_controls = ttk.Frame(rf)
        peak_controls.pack(fill=tk.X)
        ttk.Label(peak_controls, text="Порог подписей").pack(side=tk.LEFT)
        self.peak_threshold = tk.DoubleVar(value=6)
        self.peak_threshold_text = tk.StringVar(value="6 %")
        ttk.Scale(peak_controls, from_=0, to=100, variable=self.peak_threshold,
                  command=self._change_peak_threshold, takefocus=True).pack(
                      side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        ttk.Label(peak_controls, textvariable=self.peak_threshold_text,
                  width=6).pack(side=tk.LEFT)
        self.spec_canvas = tk.Canvas(rf, background=SPEC_BG, highlightthickness=0,
                                     height=230, takefocus=True)
        self.spec_canvas.pack(fill=tk.BOTH, expand=True)
        self.spec_canvas.bind("<Configure>", lambda e: self._redraw_spectrum())
        # zoom / pan: keys +-, arrows, mouse wheel and dragging
        self.spec_canvas.bind("<Button-1>", self._spec_press)
        self.spec_canvas.bind("<B1-Motion>", self._spec_drag)
        self.spec_canvas.bind("<ButtonRelease-1>", self._spec_release)
        self.spec_canvas.bind("<Double-Button-1>", lambda e: self._spec_reset())
        self.spec_canvas.bind("<MouseWheel>", self._spec_wheel)
        self.spec_canvas.bind("<Button-4>", lambda e: self._spec_zoom(1.25, e.x))
        self.spec_canvas.bind("<Button-5>", lambda e: self._spec_zoom(0.8, e.x))
        for seq in ("<plus>", "<equal>", "<KP_Add>"):
            self.spec_canvas.bind(seq, lambda e: self._spec_zoom(1.25))
        for seq in ("<minus>", "<KP_Subtract>"):
            self.spec_canvas.bind(seq, lambda e: self._spec_zoom(0.8))
        for seq, frac in (("<Left>", -0.12), ("<Right>", 0.12)):
            self.spec_canvas.bind(seq, lambda e, f=frac: self._spec_pan(f))
        self.spec_canvas.bind("<Home>", lambda e: self._spec_reset())
        self.info = tk.Text(rf, height=7, wrap="word", font=("Consolas", 9),
                            background="#f7f7f7")
        self.info.pack(fill=tk.X, pady=(3, 0))
        self.info.configure(state=tk.DISABLED)

        self.status = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.status, anchor="w",
                  relief=tk.SUNKEN, padding=(6, 2)).pack(fill=tk.X)

        logf = ttk.Frame(self)
        logf.pack(fill=tk.X, padx=6, pady=(0, 6))
        self.logbox = tk.Text(logf, height=6, wrap="word", state=tk.DISABLED)
        sb = ttk.Scrollbar(logf, command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=sb.set)
        self.logbox.pack(side=tk.LEFT, fill=tk.X, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

    # ---------------------------------------------------------------- log
    def log(self, msg):
        """Tk is not thread safe and the export runs in a worker thread, so a
        message from another thread is queued and written by the main loop."""
        if threading.current_thread() is not threading.main_thread():
            self._logq.put(str(msg))
            return
        self._write_log(str(msg))

    def _write_log(self, msg):
        self.logbox.configure(state=tk.NORMAL)
        self.logbox.insert(tk.END, msg + "\n")
        self.logbox.see(tk.END)
        self.logbox.configure(state=tk.DISABLED)
        self.update_idletasks()

    def _drain_log(self):
        try:
            while True:
                self._write_log(self._logq.get_nowait())
        except queue.Empty:
            pass
        except Exception:
            pass
        if self._export_finished:
            self._export_finished = False
            if self.project_path:
                self.btn_export.configure(state=tk.NORMAL)
        try:
            self.after(120, self._drain_log)
        except Exception:
            pass

    def on_close(self):
        cp = ui_load()
        if not cp.has_section("ui"):
            cp.add_section("ui")
        try:
            cp.set("ui", "geometry", self.geometry())
        except Exception:
            pass
        cp.set("ui", "out_dir", self.outdir)
        if self.last_dir:
            cp.set("ui", "last_dir", self.last_dir)
        ui_save(cp)
        self.destroy()

    # ------------------------------------------------------------ calibration
    def _refresh_profiles(self):
        self._profiles = Q.profiles()
        titles = [p.title for p in self._profiles]
        self.profile_box.configure(values=titles)
        active = Q.active_profile()
        for i, p in enumerate(self._profiles):
            if p.slug == active.slug:
                self.profile_box.current(i)
                break

    def on_profile_change(self, _evt=None):
        i = self.profile_box.current()
        if not (0 <= i < len(self._profiles)):
            return
        p = self._profiles[i]
        serial = self.proj.format.detector_serial if self.proj else ""
        Q.set_active_profile(p.slug, remember_for=serial)
        Q.reset_cache()
        self._std = None
        self._std_key = None
        self._refresh_mac_sets()
        self.log("Калибровка: %s (%s)" % (p.title, p.slug))
        if serial:
            self.log("  выбор запомнен для детектора S/N %s" % serial)
        if self.lst.curselection():
            self.on_analysis_select()

    # ------------------------------------------------------ absorption tables
    def _refresh_mac_sets(self):
        """fill the μ/ρ combobox; called at start and after a profile change"""
        self._mac_sets = Q.mac_sets()
        titles = [s.title for s in self._mac_sets]
        self.mac_box.configure(values=titles)
        active = Q.mac_file()
        for i, s in enumerate(self._mac_sets):
            if os.path.abspath(s.path) == os.path.abspath(active):
                self.mac_box.current(i)
                break
        else:
            if titles:
                self.mac_box.current(0)

    def on_mac_change(self, _evt=None):
        i = self.mac_box.current()
        if not (0 <= i < len(self._mac_sets)):
            return
        s = self._mac_sets[i]
        Q.set_active_mac_set(s.slug)
        Q.reset_cache()
        self.log("Коэффициенты поглощения: %s (%s)" % (s.title, os.path.basename(s.path)))
        if self.lst.curselection():
            self.on_analysis_select()

    def on_absorption_change(self):
        on = bool(self.absorb_var.get())
        Q.set_absorption_enabled(on)
        self.log("Поправка на поглощение: %s" % ("включена" if on else "выключена"))
        if self.lst.curselection():
            self.on_analysis_select()

    def show_calibration(self):
        p = Q.active_profile()
        root = Q.calibration_root()
        files = []
        for d in (p.path, root):
            if os.path.isdir(d):
                for f in sorted(os.listdir(d)):
                    if f not in files:
                        files.append(f)
        rows = "\n".join(Q.profile_lines(root))
        macs = "\n".join(Q.mac_lines(root)) or "  (нет таблиц)"
        messagebox.showinfo(
            "Калибровка",
            "Активный профиль:\n  %s (%s)\n\nПапка профиля:\n  %s\n\n"
            "Корень калибровки:\n  %s\n\n"
            "Коэффициенты поглощения μ/ρ:\n  %s\n  файл: %s\n  поправка на "
            "поглощение: %s\n\nТаблицы μ/ρ (* — активная):\n%s\n\n"
            "Файлы:\n  %s\n\n"
            "Все профили (* — активный):\n%s\n\n"
            "Профиль выбирается по серийному номеру детектора проекта; "
            "другой прибор — другая папка с xpp_standards.csv и profile.ini. "
            "Наборы μ/ρ лежат в calibration\\mac\\<имя>.csv (+ .ini с названием). "
            "Переменная окружения NEWINCA_CALIB заменяет корень калибровки."
            % (p.title, p.slug, p.path, root, Q.mac_title(), Q.mac_file(),
               "включена" if Q.absorption_enabled() else "выключена",
               macs, "\n  ".join(files) or "—", rows))

    def choose_out(self):
        d = filedialog.askdirectory(title="Папка для результатов",
                                    initialdir=self.outdir or self.last_dir or None)
        if d:
            self.outdir = d
            self.log("Папка вывода: %s" % d)

    # -------------------------------------------------------------- actions
    def _populate_recent_menu(self):
        self.recent_menu.delete(0, tk.END)
        if not self.recent_projects:
            self.recent_menu.add_command(label="Недавних проектов нет", state=tk.DISABLED)
        for path in self.recent_projects:
            label = "%s — %s" % (os.path.basename(path), os.path.dirname(path))
            self.recent_menu.add_command(label=label, command=lambda p=path: self.open_project(p))

    def open_project(self, path=None):
        if path is None:
            path = filedialog.askopenfilename(title="Открыть проект INCA",
                                              filetypes=SUPPORTED,
                                              initialdir=self.last_dir or None)
        if not path:
            return
        path = os.path.abspath(path)
        try:
            self.proj = I.open_project(path)
        except Exception as e:
            messagebox.showerror("Ошибка", "Не удалось прочитать файл:\n%s" % e)
            return
        self.project_path = path
        self.last_dir = os.path.dirname(path)
        self.recent_projects = ([path] + [other for other in self.recent_projects
                                          if other != path])[:10]
        cp = ui_load()
        if not cp.has_section("ui"):
            cp.add_section("ui")
        cp.set("ui", "recent_projects", json.dumps(self.recent_projects, ensure_ascii=False))
        cp.set("ui", "last_dir", self.last_dir)
        ui_save(cp)
        self.sidecar = ptmod.load(path, APP_DIR)
        self.records = []
        self.placed, self.lines = [], []
        self.current_fov = None
        self._spec = None
        self._show_image()
        self.lst.delete(0, tk.END)
        self.info.configure(state=tk.NORMAL)
        self.info.delete("1.0", tk.END)
        self.info.configure(state=tk.DISABLED)
        self._redraw_spectrum()
        self._std = None
        self._std_key = None
        self._fill_tree()
        self.btn_export.configure(state=tk.NORMAL)
        self.log("Открыт: %s" % path)
        for line in self.proj.format.describe():
            self.log("  " + line)
        self._auto_profile()
        if self.sidecar:
            self.log("Загружены координаты точек: %s" % ptmod.find_sidecar(path, APP_DIR))
        self.status.set(self.proj.format.line())

    def _auto_profile(self):
        if not self.proj:
            return
        p, reason = Q.suggest_for(self.proj.format)
        if p is None:
            return
        Q.set_active_profile(p.slug)
        Q.reset_cache()
        self._refresh_profiles()
        self.log("Калибровка: %s (%s) — %s" % (p.title, p.slug, reason))

    def _fill_tree(self):
        self.tree.delete(*self.tree.get_children())
        for s in self.proj.samples:
            sn = self.tree.insert("", "end", text="Образец: %s" % s.name(), open=True)
            for f in s.fovs:
                label = "%s  [%d изобр., %d анализов]" % (
                    f.name() or f.id, len(f.images), len(f.spectra))
                node = self.tree.insert(sn, "end", text=label)
                self.tree.item(node, tags=(f.id,))
        n = self.proj.format
        self.status.set("%s · %d обр., %d уч., %d спектров"
                        % (n.family_label, n.n_samples, n.n_fovs, n.n_spectra))

    def _fov_by_id(self, fid):
        for s in self.proj.samples:
            for f in s.fovs:
                if f.id == fid:
                    return f
        return None

    def on_select(self, _evt=None):
        sel = self.tree.selection()
        if not sel:
            return
        tags = self.tree.item(sel[0], "tags")
        if not tags:
            return
        f = self._fov_by_id(tags[0])
        if not f:
            return
        self.current_fov = f
        self._refresh_analysis_list()
        self._show_image()
        self.log("Выбран участок: %s" % (f.name() or f.id))

    def _refresh_analysis_list(self):
        self.lst.delete(0, tk.END)
        for i, sp in enumerate(self.current_fov.spectra, 1):
            pos = "•" if sp.points() else " "
            self.lst.insert(tk.END, "%s %2d. %s" % (pos, i, sp.id))
        positions, source = pipeline.positions_for_fov(self.current_fov, self.sidecar)
        self.placed, self.lines = [], []
        for (label, sid, xy) in positions:
            if len(xy) > 1:
                self.lines.append((label, xy))
            elif xy:
                self.placed.append((xy[0][0], xy[0][1], label, sid))
        if self.current_fov.spectra:
            self.lst.selection_set(0)
            self.on_analysis_select()
        if source == "project":
            self.log("Координаты площадок прочитаны из проекта (ChordList): %d точек"
                     % sum(len(xy) for _l, _s, xy in positions))
        elif source == "sidecar":
            self.log("В проекте координат нет — взяты из файла-спутника")
        else:
            self.log("Координаты площадок в проекте не сохранены")

    # ---------------------------------------------------------------- image
    def _show_image(self):
        f = self.current_fov
        self.canvas.delete("all")
        self._img_ref = None
        self._image_source = None
        if not f:
            return
        if not f.images:
            self._draw_card(f)
            return
        m = f.images[0].micromap()
        if not m.ok:
            self.canvas.create_text(20, 20, anchor="nw", fill="white",
                                    text="изображение участка не читается")
            return
        self._image_source = m.to_pil().convert("RGB")
        self._field_width_um = f.images[0].field_width_um()
        self._image_status = "%s — %dx%d, %d бит" % (
            f.name() or f.id, m.width, m.height, m.bits)
        self._resize_image()

    def _resize_image(self, _event=None):
        img = self._image_source
        if img is None:
            return
        self._scale = min(self.canvas.winfo_width() / img.width,
                          self.canvas.winfo_height() / img.height, 1.0)
        disp = img.resize((max(1, int(img.width * self._scale)),
                           max(1, int(img.height * self._scale))))
        from PIL import ImageTk
        self._img_ref = ImageTk.PhotoImage(disp)
        selected = self._selected_spectrum()
        if selected:
            self._highlight(selected[0])
        else:
            self._redraw_markers()
        field = ("поле съёмки %.4g мкм" % self._field_width_um
                 if self._field_width_um else "калибровка масштаба недоступна")
        self.status.set("%s · %s" % (self._image_status, field))

    def _redraw_markers(self):
        self.canvas.delete("all")
        if self._img_ref is None:
            return
        self.canvas.create_image(0, 0, anchor="nw", image=self._img_ref)
        s = self._scale
        for (lbl, xy) in self.lines:
            xs = [x * s for x, y in xy]
            ys = [y * s for x, y in xy]
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
            if x1 - x0 < 11:
                c = (x0 + x1) / 2
                x0, x1 = c - 5.5, c + 5.5
            if y1 - y0 < 11:
                c = (y0 + y1) / 2
                y0, y1 = c - 5.5, c + 5.5
            self.canvas.create_rectangle(x0 - 1, y0 - 1, x1 + 1, y1 + 1,
                                         outline="#ff3030", width=2)
            self.canvas.create_text(x1 + 6, (y0 + y1) / 2, text=str(lbl), anchor="w",
                                    fill="#ff3030", font=("Arial", 11, "bold"))
        for (x, y, lbl, _sid) in self.placed:
            px, py = x * s, y * s
            radius = render.POINT_RADIUS
            self.canvas.create_oval(px - radius, py - radius, px + radius, py + radius,
                                    outline="#ff3030", width=2)
            self.canvas.create_text(px + radius + 4, py, text=str(lbl), anchor="w",
                                    fill="#ff3030", font=("Arial", 11, "bold"))
        self._draw_scale_bar()

    def _draw_scale_bar(self):
        width, height = self._img_ref.width(), self._img_ref.height()
        if not self._field_width_um or width < 80 or height < 32:
            return
        bar = render.scale_bar(width, self._field_width_um)
        if bar is None:
            return
        pixels, text = bar
        x, y = 8, height - 7
        label = self.canvas.create_text(x, y - 5, text=text, anchor="sw",
                                        fill="white", font=("Arial", 11))
        _, top, right, _ = self.canvas.bbox(label)
        plate = self.canvas.create_rectangle(3, top - 2, max(x + pixels, right) + 5,
                                              y + 4, fill="black", width=0)
        self.canvas.tag_lower(plate, label)
        self.canvas.create_line(x, y, x + pixels, y, fill="white", width=2)
        for end in (x, x + pixels):
            self.canvas.create_line(end, y - 3, end, y + 2, fill="white")

    def _draw_card(self, f):
        state = {"points": "есть", "empty": "нет: ChordList записан пустым",
                 "none": "нет"}.get(f.chord_state(), f.chord_state())
        lines = ["Участок: %s" % (f.name() or f.id), "",
                 "В этом проекте у участка нет изображения.",
                 "Анализов: %d" % len(f.spectra),
                 "Координаты площадок: %s" % state]
        y = 24
        for i, txt in enumerate(lines):
            self.canvas.create_text(24, y, anchor="nw",
                                    fill=("#ffffff" if i < 2 else "#c8c8c8"),
                                    text=txt, font=(UI_FONT, 12,
                                                    "bold" if i < 2 else "normal"))
            y += 22
        self.status.set("%s — изображения в проекте нет" % (f.name() or f.id))

    # -------------------------------------------------------------- spectrum
    def _selected_spectrum(self):
        if not self.current_fov:
            return None
        sel = self.lst.curselection()
        if not sel:
            return None
        i = sel[0]
        if i >= len(self.current_fov.spectra):
            return None
        return i + 1, self.current_fov.spectra[i]

    def on_analysis_select(self, _evt=None):
        picked = self._selected_spectrum()
        if not picked:
            return
        n, sp = picked
        self._draw_spectrum(sp)
        self._show_info(n, sp)
        # highlight the matching area on the image
        self._highlight(n)

    def _highlight(self, n):
        self._redraw_markers()
        s = self._scale
        for (lbl, xy) in self.lines:
            if str(lbl) != str(n):
                continue
            xs = [x * s for x, y in xy]
            ys = [y * s for x, y in xy]
            self.canvas.create_rectangle(min(xs) - 3, min(ys) - 3,
                                         max(xs) + 3, max(ys) + 3,
                                         outline="#ffe14d", width=2)
        for (x, y, lbl, _sid) in self.placed:
            if str(lbl) == str(n):
                px, py = x * s, y * s
                radius = render.POINT_RADIUS + 3
                self.canvas.create_oval(px - radius, py - radius, px + radius, py + radius,
                                        outline="#ffe14d", width=2)

    def _change_peak_threshold(self, _value):
        self.peak_threshold_text.set("%d %%" % round(self.peak_threshold.get()))
        self._redraw_spectrum()

    def _draw_spectrum(self, sp):
        self._spec = (sp.counts(), [l for l in sp.labels() if 0 < l[2] < 21000])
        self._view = None                 # a new spectrum starts at full range
        self._redraw_spectrum()

    # ---- spectrum view: zoom and pan ---------------------------------------
    def _spec_limits(self):
        data = getattr(self, "_spec", None)
        if not data or len(data[0]) < 2:
            return None
        n = len(data[0])
        return I.to_energy(0) / 1000.0, I.to_energy(n - 1) / 1000.0

    def _current_view(self):
        limits = self._spec_limits()
        if not limits:
            return None
        lo, hi = self._view or limits
        return max(limits[0], lo), min(limits[1], hi)

    def _set_view(self, lo, hi):
        limits = self._spec_limits()
        if not limits:
            return
        full = limits[1] - limits[0]
        span = max(hi - lo, full / 400.0)      # stop at 1/400 of the full range
        centre = (lo + hi) / 2.0
        lo, hi = centre - span / 2.0, centre + span / 2.0
        if lo < limits[0]:
            lo, hi = limits[0], limits[0] + span
        if hi > limits[1]:
            lo, hi = limits[1] - span, limits[1]
        if span >= full:
            self._view = None
        else:
            self._view = (lo, hi)
        self._redraw_spectrum()

    def _spec_reset(self):
        self._view = None
        self._redraw_spectrum()

    def _spec_zoom(self, factor, x=None):
        view = self._current_view()
        if not view:
            return
        lo, hi = view
        if x is None:
            anchor = (lo + hi) / 2.0
        else:
            c = self.spec_canvas
            L, R = 52, 14
            w = max(c.winfo_width(), 200)
            frac = min(1.0, max(0.0, (x - L) / max(1.0, (w - R - L))))
            anchor = lo + frac * (hi - lo)
        self._set_view(anchor - (anchor - lo) / factor,
                       anchor + (hi - anchor) / factor)

    def _spec_pan(self, fraction):
        view = self._current_view()
        if not view:
            return
        lo, hi = view
        shift = (hi - lo) * fraction
        self._set_view(lo + shift, hi + shift)

    def _spec_wheel(self, event):
        self.spec_canvas.focus_set()
        delta = getattr(event, "delta", 0)
        if delta == 0:
            return
        self._spec_zoom(1.25 if delta > 0 else 0.8, event.x)

    def _spec_press(self, event):
        self.spec_canvas.focus_set()
        view = self._current_view()
        if not view:
            return
        self._drag = (event.x, view)

    def _spec_drag(self, event):
        if not getattr(self, "_drag", None):
            return
        x0, (lo, hi) = self._drag
        c = self.spec_canvas
        L, R = 52, 14
        width = max(1.0, max(c.winfo_width(), 200) - R - L)
        shift = -(event.x - x0) / width * (hi - lo)
        self._set_view(lo + shift, hi + shift)

    def _spec_release(self, _event=None):
        self._drag = None

    def _redraw_spectrum(self):
        c = self.spec_canvas
        c.delete("all")
        data = getattr(self, "_spec", None)
        w = max(c.winfo_width(), 200)
        h = max(c.winfo_height(), 120)
        if not data:
            c.create_text(10, 10, anchor="nw", fill="#778899",
                          text="выберите анализ в списке слева")
            return
        counts, labels = data
        n = len(counts)
        if n < 2:
            return
        L, R, T, B = 52, 14, 20, 30
        x0, x1, y0, y1 = L, w - R, T, h - B
        if x1 - x0 < 40 or y1 - y0 < 30:
            return
        top = max(max(counts), 1)
        logarithmic = self.spectrum_scale.current() == 0
        if logarithmic:
            ymax = math.log10(top + 1)
            ticks = [10 ** exp for exp in range(math.floor(math.log10(top)) + 1)]
        else:
            target = max(1, top / 5)
            decade = 10 ** math.floor(math.log10(target))
            tick_step = next(step * decade for step in (1, 2, 5, 10)
                             if step * decade >= target)
            ymax = math.ceil(top / tick_step) * tick_step
            ticks = range(0, ymax + 1, tick_step)

        view = self._current_view()
        if not view:
            return
        e_lo, e_hi = view
        span = e_hi - e_lo

        def X(e_kev):
            return x0 + (x1 - x0) * (e_kev - e_lo) / span

        def Y(v):
            value = math.log10(v + 1) if logarithmic else v
            return y1 - (y1 - y0) * (value / ymax)

        def E_of_ch(ch):
            return I.to_energy(ch) / 1000.0

        # grid
        for tick in ticks:
            y = Y(tick)
            c.create_line(x0, y, x1, y, fill="#243040")
            c.create_text(x0 - 6, y, text="%d" % tick, anchor="e",
                          fill=AXIS, font=(UI_FONT, 8))
        step = next((s for s in (0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0)
                     if span / s <= 12), 10.0)
        tick_e = math.ceil(e_lo / step) * step
        while tick_e <= e_hi + 1e-9:
            x = X(tick_e)
            if x >= x0:
                c.create_line(x, y0, x, y1, fill="#1b2430")
                text = ("%.2f" % tick_e) if step < 0.1 else \
                       ("%.1f" % tick_e) if step < 1 else "%g" % tick_e
                c.create_text(x, y1 + 10, text=text, fill=AXIS,
                              font=(UI_FONT, 8))
            tick_e += step
        c.create_rectangle(x0, y0, x1, y1, outline="#3a4a5e")
        c.create_text(x1, y1 + 22, text="кэВ", anchor="e", fill=AXIS,
                      font=(UI_FONT, 8))

        # spectrum: one value per pixel column (the maximum, so peaks survive)
        ch_lo = max(0, int((e_lo * 1000.0) / I.EV_PER_CH + I.ZERO_CH) - 2)
        ch_hi = min(n, int((e_hi * 1000.0) / I.EV_PER_CH + I.ZERO_CH) + 3)
        cols = max(1, int(x1 - x0))
        step_ch = max(0.05, (ch_hi - ch_lo) / cols)
        pts = []
        i = 0
        while i < cols:
            a = int(ch_lo + i * step_ch)
            b = max(a + 1, int(ch_lo + (i + 1) * step_ch))
            seg = counts[a:min(b, n)]
            if not seg:
                break
            v = max(seg)
            ch = a + seg.index(v)
            pts.extend((X(E_of_ch(ch)), Y(v)))
            i += 1
        if len(pts) > 3:
            c.create_line(*pts, fill="#7fd4ff", width=1, tags=("spectrum_curve",))

        # Keep each saved line at its own energy; only those inside the view.
        visible = []
        threshold = top * round(self.peak_threshold.get()) / 100
        for _idx, name, energy in labels:
            e = energy / 1000.0
            if not (e_lo < e < e_hi):
                continue
            ch = int(round(energy / I.EV_PER_CH + I.ZERO_CH))
            if ch < 1 or ch >= n:
                continue
            lo, hi = max(0, ch - 6), min(n, ch + 7)
            v = max(counts[lo:hi])
            if v < threshold:
                continue
            background = (counts[max(0, ch - 12)] + counts[min(n - 1, ch + 12)]) / 2
            visible.append((v - background, v, energy, name))

        for _, v, energy, name in sorted(visible, reverse=True):
            x = X(energy / 1000.0)
            label_y = max(y0 + 8, Y(v) - 8)
            label_y = min(y1 - 8, label_y)
            marker = c.create_line(x, y0, x, y1, fill="#5a4630")
            c.tag_lower(marker, "spectrum_curve")
            label = c.create_text(x, label_y, text=name, fill="#ffcf70",
                                  font=(UI_FONT, 9, "bold"))
            left, _, right, _ = c.bbox(label)
            c.move(label, max(0, x0 - left) - max(0, right - x1), 0)
            # Try the rows nearest the peak until the text clears nearby labels.
            left, upper, right, lower = c.bbox(label)
            row_height = lower - upper + 2
            rows_above = range(0, math.floor(max(0, upper - y0) / row_height) + 1)
            rows_below = range(1, math.floor(max(0, y1 - lower) / row_height) + 1)
            offsets = [-row * row_height for row in rows_above]
            offsets.extend(row * row_height for row in rows_below)
            for offset in offsets:
                nearby = c.find_overlapping(left, upper + offset, right, lower + offset)
                if all("peak_label" not in c.gettags(item) for item in nearby):
                    c.move(label, 0, offset)
                    break
            c.addtag_withtag("peak_label", label)

        hint = "колесо / +- масштаб, ←→ сдвиг, двойной щелчок — весь спектр"
        c.create_text(x0, 6, anchor="w", fill=AXIS, font=(UI_FONT, 8),
                      text="%d каналов · %.2f–%.2f кэВ · %s"
                      % (n, e_lo, e_hi, hint))

    def _show_info(self, n, sp):
        em = sp.em_conditions()
        info = sp.stream("information")
        inf = I.parse_information(info) if info else {}
        counts = sp.counts()
        lines = []
        lines.append("Участок: %s   анализ №%d   %s"
                     % (self.current_fov.name() or self.current_fov.id, n, sp.id))
        bits = []
        if em.get("kv"):
            bits.append("%.0f кВ" % em["kv"])
        if inf.get("live_s"):
            bits.append("живое время %.1f с" % inf["live_s"])
        if inf.get("real_s"):
            bits.append("реальное %.1f с" % inf["real_s"])
        if inf.get("date"):
            bits.append(inf["date"].strftime("%Y-%m-%d %H:%M"))
        lines.append("   ".join(bits))
        lines.append("Всего импульсов: %d   максимум: %d"
                     % (sum(counts), max(counts) if counts else 0))
        labels = sp.labels()
        on_spectrum = analysis.labelled_elements(sp)
        if on_spectrum:
            lines.append("На спектре: " + ", ".join(on_spectrum))
        if labels:
            lines.append("Метки INCA: " + ", ".join("%s %.3f кэВ" % (name, ev / 1000)
                                                   for _, name, ev in labels))
        if analysis.is_failed(counts):
            lines.append("Расчёт: неудачная съёмка (нет пиков выше нуль-пика)")
        else:
            try:
                std = self._standards()
                if not std:
                    lines.append("Расчёт: нет калибровочных данных")
                else:
                    comp, ox, details = analysis.quantify_spectrum(sp, std)
                    # exactly the elements of the spectrum, in their own order:
                    # an element is either a number here or explicitly explained
                    parts, rest = [], []
                    for sym in details["quantified"]:
                        name = analysis.EL[sym][2]
                        if not name:
                            continue          # C, O, F, Cl: shown below as elements
                        value = ox.get(name, 0.0) * 100
                        if value >= 0.005:
                            parts.append("%s %.2f" % (name, value))
                        else:
                            rest.append("%s <0.01" % name)
                    if parts:
                        lines.append("Состав, мас.%: " + "   ".join(parts))
                        total = sum(ox.values()) * 100 if ox else 0.0
                        lines.append("Сумма оксидов: %.2f" % total)
                    else:
                        lines.append("Состав: пиков выше порога нет")
                    if rest:
                        lines.append("Ниже 0.01: " + ", ".join(rest))
                    no_ox = [s for s in details["no_oxide"]
                             if comp.get(s, 0) * 100 >= 0.005]
                    if no_ox:
                        lines.append("Без оксида (элемент): "
                                     + ", ".join("%s %.2f" % (s, comp[s] * 100)
                                                 for s in no_ox))
                    if details["no_standard"]:
                        lines.append("Нет стандарта в этой калибровке: "
                                     + ", ".join(details["no_standard"]))
            except Exception as e:
                lines.append("Расчёт недоступен: %s" % e)
        lines.append("μ/ρ: %s; поправка на поглощение %s"
                     % (Q.mac_title(),
                        "включена" if Q.absorption_enabled() else "выключена"))
        self.info.configure(state=tk.NORMAL)
        self.info.delete("1.0", tk.END)
        self.info.insert("1.0", "\n".join(lines))
        self.info.configure(state=tk.DISABLED)

    def _standards(self):
        prof = Q.active_profile()
        e0 = None
        if self.proj:
            for _f, sp in self.proj.all_spectra():
                e0 = sp.em_conditions().get("kV")
                if e0:
                    break
        key = (prof.slug, e0)
        if self._std_key != key:
            self._std = analysis.load_standards(cal_dir=prof.path, e0=e0)
            self._std_key = key
        return self._std

    # --------------------------------------------------------------- export
    def export_all(self):
        if not self.project_path:
            return
        self.btn_export.configure(state=tk.DISABLED)
        self.log("Запуск обработки…")

        def work():
            try:
                res = pipeline.export_project(self.project_path, self.outdir,
                                              app_dir=APP_DIR, log=self.log)
                self.res = res
                self.log("Импорт завершён.")
            except Exception:
                self.log("ОШИБКА:\n" + traceback.format_exc())
            finally:
                self._export_finished = True

        threading.Thread(target=work, daemon=True).start()


def run():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    run()
