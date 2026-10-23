# PyInstaller build for the standalone app:  pyinstaller imgbake.spec
# Windows/Linux: one self-contained executable in dist/.  macOS: dist/Imgbake.app.
import os, sys

linux = sys.platform.startswith("linux")
binaries = []
if linux:
    # Qt's X11 plugin needs libxcb-cursor and these helpers, which many desktops don't install: bring them
    # along, next to Qt's own libraries where it looks first ($ORIGIN). Nothing graphics-related in them.
    dirs = [d for d in os.environ.get("LD_LIBRARY_PATH", "").split(":") if d] + \
           ["/usr/lib/x86_64-linux-gnu", "/usr/lib64", "/usr/lib"]
    for name in ("libxcb-cursor.so.0", "libxcb-icccm.so.4", "libxcb-image.so.0", "libxcb-keysyms.so.1",
                 "libxcb-render-util.so.0", "libxcb-util.so.1"):
        found = [os.path.join(d, name) for d in dirs if os.path.exists(os.path.join(d, name))]
        if not found: sys.exit(f"{name} not found: install it to bundle it (see .github/workflows/release.yml)")
        binaries.append((found[0], "PyQt6/Qt6/lib"))

a = Analysis(["imgbake.py"], binaries=binaries, datas=[("icon.png", ".")],
             hiddenimports=["webview.platforms.qt", "qtpy", "PyQt6.QtWebEngineWidgets", "PyQt6.QtWebEngineCore",
                            "PyQt6.QtWebChannel", "PyQt6.QtNetwork"] if linux else [],
             # the Linux app draws its window with the bundled Qt, never the system's GTK
             excludes=["gi", "PyQt5", "PySide2", "PySide6", "IPython", "pytest"])
if linux:
    # PyQt6 brings all of Qt (3D, multimedia, sensors…): keep what the web view and its plugins link to
    # (ldd on QtWebEngineWidgets and plugins/*/*.so), English only. That halves the download.
    import re
    QT_KEEP = {"Core", "DBus", "EglFSDeviceIntegration", "Gui", "Network", "OpenGL", "OpenGLWidgets", "Pdf",
               "Positioning", "PrintSupport", "Qml", "QmlMeta", "QmlModels", "QmlWorkerScript", "Quick",
               "QuickWidgets", "SerialPort", "Svg", "WaylandClient", "WebChannel", "WebEngineCore",
               "WebEngineWidgets", "Widgets", "WlShellIntegration", "XcbQpa"}

    # Libraries every Linux desktop has, and whose system copies must be used: they have to match its
    # graphics driver and GTK. (The Ubuntu 22.04 libstdc++ that CI would bundle is too old for newer Mesa
    # drivers, which then fail to load, so Qt finds no GLX and aborts.) Only top-level ones: Pillow's
    # own copies live in pillow.libs/ under hashed names.
    SYSTEM = re.compile(r"(libstdc\+\+|libgcc_s|libgbm|libdrm|libexpat|libz|libglib-2\.0|libgio-2\.0|"
                        r"libgobject-2\.0|libgmodule-2\.0|libgthread-2\.0|libpcre2-8|libmount|libblkid|libselinux|"
                        r"libX11|libX11-xcb|libXau|libXdmcp|libXext|libXfixes|libXrandr|libXrender|libXcomposite|"
                        r"libXdamage|libXtst|libxcb(-[a-z0-9-]+)?|libxkbcommon(-x11)?|libfontconfig|"
                        r"libfreetype|libdbus-1|libsystemd|libudev|libasound|libatk-1\.0|libgcrypt|libgpg-error|"
                        r"libcap|liblz4)\.so(\.\d+)*")

    def wanted(dest):
        dest = dest.replace("\\", "/")
        if "/" not in dest and SYSTEM.fullmatch(dest): return False
        m = re.search(r"libQt6(\w+)\.so", dest)
        if m: return m.group(1) in QT_KEEP
        if "/Qt6/translations/" in dest: return dest.endswith("/en-US.pak")
        return "/Qt6/qml/" not in dest
    a.binaries = [b for b in a.binaries if wanted(b[0])]
    a.datas = [d for d in a.datas if wanted(d[0])]

pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Imgbake", console=False)
    coll = COLLECT(exe, a.binaries, a.datas, name="Imgbake")
    app = BUNDLE(coll, name="Imgbake.app", icon="icon.png", bundle_identifier="com.imgbake.app",
                 info_plist={"NSHighResolutionCapable": True})
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="Imgbake", icon="icon.png", console=False, upx=False)
