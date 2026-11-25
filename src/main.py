# -*- coding: utf-8 -*-
"""
newINCA -- read INCA .ipj / .ipl project files without INCA.

    newINCA.exe                       graphical interface
    newINCA.exe list     <project>    tree of samples, sites, images, spectra
    newINCA.exe info     <project>    detected format, detector, profile (--json)
    newINCA.exe export   <project> [dir] [--profile NAME]
    newINCA.exe quant    <project> [dir] [--profile NAME]
    newINCA.exe profiles              calibration profiles available
    newINCA.exe selftest <file|dir>   read every project and report problems
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from version import __version__

CLI_COMMANDS = {"list", "info", "profiles", "export", "quant", "selftest"}


def _safe_streams():
    """A windowed executable has no stdout/stderr; never let print() crash.
    A console build is switched to UTF-8 so Cyrillic is correct both on screen
    and when the output is redirected to a file."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    try:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
    for name in ("stdout", "stderr"):
        st = getattr(sys, name, None)
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main():
    args = sys.argv[1:]
    if args and args[0].lower() in CLI_COMMANDS:
        _safe_streams()
        import cli
        return cli.main(args)
    if args and args[0] in ("-h", "--help", "/?"):
        _safe_streams()
        print(__doc__)
        return 0
    if args and args[0] in ("-V", "--version"):
        _safe_streams()
        print("newINCA %s" % __version__)
        return 0
    import app
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
