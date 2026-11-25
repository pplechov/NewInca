# -*- coding: utf-8 -*-
"""build.py -- сборка newINCA из одного исходного кода на любой системе.

    python build.py            собрать в dist/ и упаковать zip
    python build.py --no-zip   только собрать
    python build.py --clean    удалить build/ и dist/ и выйти

Windows  : dist/newINCA-win64/       newINCA.exe, newINCA-cli.exe, _internal
macOS    : dist/newINCA.app          один бандл; CLI -- через Contents/MacOS
Linux    : dist/newINCA-linux-x86_64/ один исполняемый файл newINCA

Требуется Python 3.12 и пакеты pyinstaller, pillow, numpy.
"""
import os
import platform
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
DIST = os.path.join(ROOT, "dist")
BUILD = os.path.join(ROOT, "build")

sys.path.insert(0, SRC)
from version import __version__  # noqa: E402

SEP = ";" if os.name == "nt" else ":"
DATA_DIRS = ["calibration", "points", "tools"]


def log(msg):
    """Print on consoles whose code page has no Cyrillic (a CI runner, cmd.exe)."""
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(msg.encode(enc, "replace").decode(enc, "replace"), flush=True)


def add_data_args():
    """The calibration is data, not code, so it travels with the program."""
    args = []
    for d in DATA_DIRS:
        path = os.path.join(ROOT, d)
        if os.path.isdir(path):
            args += ["--add-data", "%s%s%s" % (path, SEP, d)]
    return args


def pyinstaller(*args):
    os.makedirs(os.path.join(BUILD, "spec"), exist_ok=True)
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean"] + list(args)
    log("$ " + " ".join(cmd[1:]))
    subprocess.run(cmd, cwd=ROOT, check=True)


def copy_tree(src, dst):
    if not os.path.isdir(src):
        return
    target = os.path.join(dst, os.path.basename(src))
    if os.path.isdir(target):
        shutil.rmtree(target)
    shutil.copytree(src, target)


def assemble_windows():
    """Two executables share one _internal, exactly as the 1.0.0 release did."""
    out = os.path.join(DIST, "newINCA-win64")
    gui = os.path.join(BUILD, "pyi-gui")
    cli = os.path.join(BUILD, "pyi-cli")
    pyinstaller("--onedir", "--noconsole", "--name", "newINCA",
                "--distpath", gui, "--workpath", os.path.join(BUILD, "work-gui"),
                "--specpath", os.path.join(BUILD, "spec"),
                os.path.join(SRC, "main.py"))
    pyinstaller("--onedir", "--console", "--name", "newINCA-cli",
                "--distpath", cli, "--workpath", os.path.join(BUILD, "work-cli"),
                "--specpath", os.path.join(BUILD, "spec"),
                os.path.join(SRC, "main.py"))

    if os.path.isdir(out):
        shutil.rmtree(out)
    shutil.copytree(os.path.join(gui, "newINCA"), out)
    shutil.copy2(os.path.join(cli, "newINCA-cli", "newINCA-cli.exe"),
                 os.path.join(out, "newINCA-cli.exe"))
    for name in ("README.md", "run-cli.bat"):
        path = os.path.join(ROOT, name)
        if os.path.isfile(path):
            shutil.copy2(path, out)
    for d in DATA_DIRS:
        copy_tree(os.path.join(ROOT, d), out)
    return out


def assemble_macos():
    app = os.path.join(DIST, "newINCA.app")
    pyinstaller("--onedir", "--windowed", "--name", "newINCA",
                "--osx-bundle-identifier", "io.github.pplechov.newinca",
                "--distpath", DIST, "--workpath", os.path.join(BUILD, "work-mac"),
                "--specpath", os.path.join(BUILD, "spec"),
                *add_data_args(), os.path.join(SRC, "main.py"))
    if not os.path.isdir(app):
        raise SystemExit("PyInstaller не создал dist/newINCA.app")
    return app


def assemble_linux():
    machine = platform.machine() or "x86_64"
    out = os.path.join(DIST, "newINCA-linux-%s" % machine)
    pyinstaller("--onedir", "--console", "--name", "newINCA",
                "--distpath", BUILD, "--workpath", os.path.join(BUILD, "work-linux"),
                "--specpath", os.path.join(BUILD, "spec"),
                *add_data_args(), os.path.join(SRC, "main.py"))
    if os.path.isdir(out):
        shutil.rmtree(out)
    shutil.copytree(os.path.join(BUILD, "newINCA"), out)
    for name in ("README.md",):
        path = os.path.join(ROOT, name)
        if os.path.isfile(path):
            shutil.copy2(path, out)
    return out


def zip_folder(top, target, root_in_zip):
    import zipfile
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for base, _dirs, files in os.walk(top):
            for f in files:
                full = os.path.join(base, f)
                rel = os.path.join(root_in_zip,
                                   os.path.relpath(full, top)).replace(os.sep, "/")
                if os.path.islink(full):
                    continue
                z.write(full, rel)
                count += 1
    log("zip: %s (файлов %d, %.1f МБ)" % (target, count,
                                          os.path.getsize(target) / 1024 / 1024))


def package(target, kind):
    """A .app must keep its symlinks and permissions: use ditto on macOS."""
    if kind == "app":
        out = os.path.join(DIST, "newINCA-%s-macOS-%s.zip" % (
            __version__, platform.machine() or "arm64"))
        if os.path.exists(out):
            os.remove(out)
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
                        target, out], check=True)
        log("zip: %s (%.1f МБ)" % (out, os.path.getsize(out) / 1024 / 1024))
        return out
    suffix = {"win": "win64", "linux": "linux-%s" % (platform.machine() or "x86_64")}
    out = os.path.join(DIST, "newINCA-%s-%s.zip" % (
        __version__, suffix["win" if os.name == "nt" else "linux"]))
    zip_folder(target, out, "newINCA")
    return out


def main(argv):
    if "--clean" in argv:
        for d in (BUILD, DIST):
            if os.path.isdir(d):
                shutil.rmtree(d)
        log("удалены build/ и dist/")
        return 0

    os.makedirs(BUILD, exist_ok=True)
    log("newINCA %s · Python %s · %s" % (__version__, platform.python_version(),
                                         sys.platform))
    if os.name == "nt":
        target, kind = assemble_windows(), "dir"
    elif sys.platform == "darwin":
        target, kind = assemble_macos(), "app"
    else:
        target, kind = assemble_linux(), "dir"

    log("готово: %s" % target)
    if "--no-zip" not in argv:
        package(target, kind)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
