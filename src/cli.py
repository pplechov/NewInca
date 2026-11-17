# -*- coding: utf-8 -*-
"""cli.py -- headless operation, also used for automated testing."""
import os, sys, json

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def usage():
    return ("использование:\n"
            "  newINCA.exe list     <проект.ipj>\n"
            "  newINCA.exe info     <проект.ipj> [--json]\n"
            "  newINCA.exe export   <проект.ipj> [папка] [--profile ИМЯ]\n"
            "  newINCA.exe quant    <проект.ipj> [папка] [--profile ИМЯ]\n"
            "  newINCA.exe profiles\n"
            "  newINCA.exe selftest <файл.ipj | папка> ...\n")


def _parse(argv):
    """split a command line into positionals and the known switches"""
    args, opts = [], {"profile": None, "json": False}
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
        elif a in ("--json", "-j"):
            opts["json"] = True
            i += 1
            continue
        args.append(a)
        i += 1
    return args, opts


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

    if len(args) < 2:
        print(usage())
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
