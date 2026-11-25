# -*- coding: utf-8 -*-
"""cli.py -- headless operation, also used for automated testing."""
import os, sys, json

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def prog():
    """The name the user actually types: newINCA.exe, newINCA, or main.py."""
    if getattr(sys, "frozen", False):
        return os.path.basename(sys.executable)
    return "newINCA"


def usage():
    p = prog()
    return ("использование:\n"
            "  %s list     <проект.ipj>\n"
            "  %s info     <проект.ipj> [--json]\n"
            "  %s export   <проект.ipj> [папка] [--profile ИМЯ]\n"
            "  %s quant    <проект.ipj> [папка] [--profile ИМЯ]\n"
            "  %s profiles\n"
            "  %s macs     [--mac ИМЯ] [--absorption вкл|выкл]\n"
            "  %s selftest <файл.ipj | папка> ...\n" % ((p,) * 7))


def _parse(argv):
    """split a command line into positionals and the known switches"""
    args, opts = [], {"profile": None, "json": False, "mac": None,
                      "absorption": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--profile", "-p"):
            if i + 1 < len(argv):
                opts["profile"] = argv[i + 1]
                i += 2
                continue
        elif a.startswith("--profile="):
            opts["profile"] = a.split("=", 1)[1]
            i += 1
            continue
        elif a in ("--mac", "-m"):
            if i + 1 < len(argv):
                opts["mac"] = argv[i + 1]
                i += 2
                continue
        elif a.startswith("--mac="):
            opts["mac"] = a.split("=", 1)[1]
            i += 1
            continue
        elif a in ("--absorption", "-a"):
            if i + 1 < len(argv):
                opts["absorption"] = argv[i + 1]
                i += 2
                continue
        elif a.startswith("--absorption="):
            opts["absorption"] = a.split("=", 1)[1]
            i += 1
            continue
        elif a in ("--json", "-j"):
            opts["json"] = True
            i += 1
            continue
        args.append(a)
        i += 1
    return args, opts


def _apply_mac(opts):
    """honour --mac / --absorption without touching the saved settings"""
    import quant as Q
    if opts.get("mac"):
        s = Q.mac_set(opts["mac"])
        if s is None:
            print("нет таблицы μ/ρ «%s»; есть: %s"
                  % (opts["mac"], ", ".join(x.slug for x in Q.mac_sets())))
            return False
        Q.set_active_mac_set(s.slug)
        Q.reset_cache()
    if opts.get("absorption") is not None:
        on = str(opts["absorption"]).strip().lower() in ("1", "on", "yes", "true",
                                                         "вкл", "да")
        Q.set_absorption_enabled(on)
    return True


def cmd_info(path, as_json=False):
    import inca_ipj as I
    import quant as Q
    import pipeline
    proj = I.open_project(path)
    fmt = proj.format
    prof, reason = pipeline.resolve_profile(fmt)
    if as_json:
        d = fmt.as_dict()
        d["file"] = os.path.abspath(path)
        d["profile"] = prof.slug
        d["profile_title"] = prof.title
        d["profile_reason"] = reason
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 0
    print(os.path.basename(path))
    for line in fmt.describe():
        print("  " + line)
    print("  Профиль    : %s (%s) — %s" % (prof.title, prof.slug, reason))
    print("  Калибровка : %s" % prof.standards_path)
    print("  μ/ρ        : %s (%s)" % (Q.mac_title(), Q.mac_file()))
    print("  Поглощение : %s" % ("поправка включена" if Q.absorption_enabled()
                                 else "поправка выключена"))
    return 0


def main(argv):
    args, opts = _parse(argv)
    if not args:
        print(usage())
        return 1
    cmd = args[0].lower()

    if cmd == "profiles":
        import quant as Q
        print("корень калибровки: %s" % Q.calibration_root())
        for line in Q.profile_lines():
            print(line)
        return 0

    if cmd == "macs":
        import quant as Q
        if not _apply_mac(opts):
            return 1
        print("корень калибровки: %s" % Q.calibration_root())
        for line in Q.mac_lines():
            print(line)
        print("поправка на поглощение: %s"
              % ("включена" if Q.absorption_enabled() else "выключена"))
        return 0

    if len(args) < 2:
        print(usage())
        return 1
    if not _apply_mac(opts):
        return 1
    proj = args[1]
    out = args[2] if len(args) > 2 else os.path.join(os.getcwd(), "out")

    import inca_ipj as I
    if cmd == "list":
        I.Project(proj).summary()
        return 0
    if cmd == "info":
        return cmd_info(proj, opts["json"])
    if cmd == "selftest":
        import selftest
        return selftest.run(args[1:])
    if cmd == "export":
        import pipeline
        res = pipeline.export_project(proj, out, app_dir=pipeline.app_dir(),
                                      do_quant=True,
                                      profile=opts["profile"])
        print("изображений: %d, точек: %d, калибровка: %s (%s)"
              % (res["images"], res["points"], res["profile"], res["profile_title"]))
        return 0
    if cmd == "quant":
        import analysis
        import pipeline
        import quant as Q
        pr = I.open_project(proj)
        prof, reason = pipeline.resolve_profile(pr.format, opts["profile"])
        recs = analysis.analyse_project(project=pr, cal_dir=prof.path)
        os.makedirs(out, exist_ok=True)
        analysis.write_csv(recs, os.path.join(out, "quant.csv"))
        ok = [r for r in recs if not r.get("failed")]
        print("калибровка: %s (%s) — %s" % (prof.title, prof.slug, reason))
        print("анализов: %d пригодных, %d неудачных" % (len(ok), len(recs) - len(ok)))
        for r in recs:
            print("  %-18s %-14s %s" % (r["site"][:18], r["spec"][:14],
                                         analysis.summary_line(r)))
        return 0
    print(usage())
    return 1
