# PyInstaller build for the standalone app:  pyinstaller imgbake.spec
# Windows/Linux: one self-contained executable in dist/.  macOS: dist/Imgbake.app.
import os, sys

linux = sys.platform.startswith("linux")
binaries = []
if linux:
    # Qt's X11 plugin needs libxcb-cursor, which many desktops don't install: bring it along
    for d in ("/usr/lib/x86_64-linux-gnu", "/usr/lib64", "/usr/lib"):
        if os.path.exists(os.path.join(d, "libxcb-cursor.so.0")):
            binaries.append((os.path.join(d, "libxcb-cursor.so.0"), ".")); break

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

    def wanted(dest):
        dest = dest.replace("\\", "/")
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
