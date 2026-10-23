#!/usr/bin/env python3
"""
imgbake.py - a simple photo editor: rotate, flip, straighten, crop, adjust brightness, contrast,
saturation and warmth, then export a resized JPEG, PNG or WebP. Opens in its own window.

The window is a web page (pywebview: Edge WebView2 on Windows, WebKit on macOS, Qt or GTK on Linux)
talking to a small local server in this file. The page shows a live preview from a downscaled copy;
Export applies the same edit to the full-resolution image with Pillow, keeping the colour profile
and (optionally) the camera info.

App:      ready-to-run Windows/macOS/Linux downloads, no Python needed - see README.md
Start:    python imgbake.py                  # open the window, pick an image in it
          python imgbake.py photo.jpg        # open the window with that image
          python imgbake.py --browser        # use your web browser instead of a window
Packages: pillow pywebview (+ PyQt6 PyQt6-WebEngine qtpy on Linux without GTK WebKit). It offers to
          install them, in a private .venv if the system Python is locked or has no pip.
Outputs:  <name>_edited.<jpg|png|webp> next to the original. Never overwrites: a repeat export gets
          _2, _3... The original is never changed.
"""
import sys, importlib.util, os, shutil, subprocess

FROZEN = getattr(sys, "frozen", False)   # running as the packaged app (PyInstaller), not a script

if sys.argv[1:2] == ["--pick-file"]:
    # helper process for the native file dialog in browser mode (see pick_file); writes the choice to a
    # file because a packaged app without a console has no usable stdout
    import tkinter as tk
    from tkinter import filedialog
    r = tk.Tk(); r.withdraw()
    try: r.attributes("-topmost", True)
    except Exception: pass
    p = filedialog.askopenfilename(title="Open image", initialdir=sys.argv[2] or None,
                                   filetypes=[("Images", "*.jpg *.jpeg *.png *.webp *.bmp *.gif *.tif *.tiff"),
                                              ("All files", "*.*")])
    with open(sys.argv[3], "w", encoding="utf-8") as f: f.write(p or "")
    sys.exit(0)


# ---------------------------------------------------------------- dependency check
def _linux_backend():
    """A GUI toolkit pywebview can draw its window with on Linux: GTK WebKit (system) or Qt WebEngine (pip)."""
    if importlib.util.find_spec("PyQt6") and importlib.util.find_spec("PyQt6.QtWebEngineWidgets") and importlib.util.find_spec("qtpy"):
        return True
    try:
        import gi
        try: gi.require_version("WebKit2", "4.1")
        except ValueError: gi.require_version("WebKit2", "4.0")
        return True
    except Exception:
        return False


REQUIRED = {"PIL": ["pillow"], "webview": ["pywebview"]}
_missing = [pkg for mod, pkgs in REQUIRED.items() if importlib.util.find_spec(mod) is None for pkg in pkgs]
if sys.platform.startswith("linux") and not FROZEN and not _linux_backend():
    _missing += ["PyQt6", "PyQt6-WebEngine", "qtpy"]
_here = os.path.dirname(os.path.abspath(__file__))
_venv = os.path.join(_here, ".venv")
_venv_py = os.path.join(_venv, "Scripts", "python.exe") if os.name == "nt" else os.path.join(_venv, "bin", "python")
if _missing and os.path.exists(_venv_py) and os.path.abspath(sys.prefix) != _venv:
    # packages live in the project's virtualenv - rerun with that Python
    sys.exit(subprocess.call([_venv_py, os.path.abspath(__file__), *sys.argv[1:]]))


def _ask(question):
    if not (sys.stdin and sys.stdin.isatty()): return False
    try: return input(question).strip().lower() in ("", "y", "yes")
    except EOFError: return False


def _system_pip_usable():
    """pip exists and the OS allows installing into this Python (PEP 668 locks many distros' Pythons)."""
    import sysconfig
    if importlib.util.find_spec("pip") is None: return False
    locked = os.path.exists(os.path.join(sysconfig.get_path("stdlib"), "EXTERNALLY-MANAGED"))
    return sys.prefix != sys.base_prefix or not locked


if _missing:
    rerun = subprocess.list2cmdline([os.path.basename(sys.executable), os.path.relpath(__file__)])
    print("Imgbake needs some Python packages that aren't installed:\n    " + ", ".join(_missing))
    if _system_pip_usable():
        cmd = [sys.executable, "-m", "pip", "install", *_missing]
        if _ask("\nInstall them now? [Y/n] "):
            if subprocess.call(cmd) == 0:
                print("\nInstalled. Starting…\n")
                sys.exit(subprocess.call([sys.executable, *sys.argv]))   # restart with packages available
            print("\nInstall failed - see the error above.")
        print("\nTo install manually, run:\n\n    " + subprocess.list2cmdline(cmd) + "\n")
        sys.exit(1)

    # this Python can't take packages directly, so give Imgbake its own virtual environment
    rel = os.path.relpath(_venv)
    print(f"\nImgbake can install them into its own virtual environment ({rel}),"
          " leaving your system Python untouched.")
    if not _ask("Set it up now? [Y/n] "):
        print(f"\nRun {rerun} again in a terminal and answer Y to set it up.\n")
        sys.exit(1)
    existed = os.path.exists(_venv)
    if subprocess.call([sys.executable, "-m", "venv", _venv]) != 0:
        if not existed: shutil.rmtree(_venv, ignore_errors=True)   # don't leave a half-made venv behind
        print("\nCouldn't create the virtual environment: Python's venv support isn't installed.")
        if shutil.which("apt"):
            print("Install it with:\n\n    sudo apt install python3-venv\n")
        else:
            print("Install your system's python3-venv package (or equivalent).\n")
        print(f"Then run Imgbake again:\n\n    {rerun}\n")
        sys.exit(1)
    if subprocess.call([_venv_py, "-m", "pip", "install", *_missing]) != 0:
        print(f"\nInstall failed - see the error above. Once it's fixed, run Imgbake again:\n\n    {rerun}\n")
        sys.exit(1)
    print("\nInstalled. Starting…\n")
    sys.exit(subprocess.call([_venv_py, os.path.abspath(__file__), *sys.argv[1:]]))

import argparse, io, json, math, secrets, tempfile, threading, time, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 500_000_000      # big panoramas are fine; only refuse the truly absurd

OPEN_EXT = ("jpg", "jpeg", "png", "webp", "bmp", "gif", "tif", "tiff")
FORMATS = {"jpg": ("JPEG", ".jpg"), "png": ("PNG", ".png"), "webp": ("WEBP", ".webp")}
ADJ = ("brightness", "contrast", "saturation", "warmth")
PREVIEW = 2000                            # long edge of the copy the window edits live


# ---------------------------------------------------------------- image editing
def load(path):
    """Open an image upright (EXIF orientation applied), as 8-bit RGB or RGBA.
    Returns (image, meta) where meta keeps what export carries over: format, ICC profile, EXIF."""
    with Image.open(path) as im:
        fmt = im.format or ""
        im.load()
        im = ImageOps.exif_transpose(im)    # also drops the orientation tag from its EXIF
    exif = im.getexif()
    meta = {"format": fmt, "icc": im.info.get("icc_profile"), "exif": exif.tobytes() if len(exif) else None}
    if im.mode.startswith("I;16") or im.mode == "I":      # 16-bit greyscale
        im = im.convert("I").point(lambda v: v * (1 / 256)).convert("L")
    alpha = im.mode in ("RGBA", "LA", "PA", "RGBa", "La") or (im.mode == "P" and "transparency" in im.info)
    im = im.convert("RGBA" if alpha else "RGB")
    if alpha and im.getchannel("A").getextrema() == (255, 255):
        im = im.convert("RGB")             # an alpha channel with nothing see-through in it
    return im, meta


def tone_lut(brightness=0, contrast=0, warmth=0):
    """Lookup table (R, G, B) for the tone sliders, each -100..100. Mirrors lut() in the page's script,
    so the preview and the export match."""
    g = 2 ** (-brightness / 100)          # midtone gamma: brightens without clipping the highlights
    k = 2 ** (contrast / 100)             # slope around mid grey
    w = warmth / 100
    out = []
    for gain in (1 + 0.15 * w, 1.0, 1 - 0.15 * w):
        for v in range(256):
            y = (((v / 255) ** g - 0.5) * k + 0.5) * gain * 255
            out.append(0 if y < 0 else 255 if y > 255 else int(y + 0.5))
    return out


def adjust(im, adj):
    """Brightness, contrast, warmth (one lookup table) then saturation (blend with luminance)."""
    a = {k: float(adj.get(k, 0) or 0) for k in ADJ}
    if not any(a.values()): return im
    alpha = im.getchannel("A") if im.mode == "RGBA" else None
    rgb = im.convert("RGB").point(tone_lut(a["brightness"], a["contrast"], a["warmth"]))
    s = 1 + a["saturation"] / 100
    if s != 1:
        grey = rgb.convert("L", (0.2126, 0.7152, 0.0722, 0)).convert("RGB")
        rgb = Image.blend(grey, rgb, s)   # s > 1 extrapolates away from grey: more saturated
    if alpha is not None: rgb.putalpha(alpha)
    return rgb


def clean_edit(e):
    """The page's edit, checked: quarter turns, flips, straighten angle, crop box, adjustments."""
    e = e or {}
    crop = [min(1.0, max(0.0, float(v))) for v in (e.get("crop") or [0, 0, 1, 1])]
    if len(crop) != 4 or crop[2] <= crop[0] or crop[3] <= crop[1]: crop = [0, 0, 1, 1]
    return {"turns": int(e.get("turns", 0)) % 4, "fh": bool(e.get("fh")), "fv": bool(e.get("fv")),
            "angle": max(-45.0, min(45.0, float(e.get("angle", 0) or 0))), "crop": crop,
            "adj": {k: max(-100.0, min(100.0, float((e.get("adj") or {}).get(k, 0) or 0))) for k in ADJ}}


def render(im, edit, scale=1.0):
    """Apply an edit at full resolution: quarter turns -> flips -> straighten -> crop -> resize -> adjust.
    Positive turns and angles are clockwise; flips are as seen on screen after the turns.
    The crop box is x0,y0,x1,y1 as fractions of the straightened (expanded) image."""
    e = clean_edit(edit)
    k = e["turns"]
    if k: im = im.transpose((None, Image.Transpose.ROTATE_270, Image.Transpose.ROTATE_180, Image.Transpose.ROTATE_90)[k])
    if e["fh"]: im = im.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if e["fv"]: im = im.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    opaque = im.mode == "RGB"
    if e["angle"]:   # rotate with see-through corners; they only survive if the crop reaches them
        im = im.convert("RGBA").rotate(-e["angle"], Image.Resampling.BICUBIC, expand=True, fillcolor=(0, 0, 0, 0))
    W, H = im.size
    x0, y0, x1, y1 = e["crop"]
    box = (round(x0 * W), round(y0 * H), round(x1 * W), round(y1 * H))
    box = (min(box[0], W - 1), min(box[1], H - 1), max(box[2], box[0] + 1), max(box[3], box[1] + 1))
    if box != (0, 0, W, H): im = im.crop(box)
    if opaque and im.mode == "RGBA" and im.getchannel("A").getextrema()[0] >= 250:
        im = im.convert("RGB")             # crop stayed inside the picture: drop the edge-antialias alpha
    scale = min(1.0, max(0.01, float(scale)))
    if scale < 1:
        im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.Resampling.LANCZOS)
    return adjust(im, e["adj"])


def unique_path(base, ext):
    """base+ext, or base_2+ext, base_3+ext... whichever doesn't exist yet."""
    p, i = base + ext, 2
    while os.path.exists(p):
        p = f"{base}_{i}{ext}"; i += 1
    return p


def save(im, path, fmt="jpg", quality=90, meta=None, keep_info=True):
    kind = FORMATS[fmt][0]
    meta = meta or {}
    kw = {}
    if meta.get("icc"): kw["icc_profile"] = meta["icc"]
    if keep_info and meta.get("exif"): kw["exif"] = meta["exif"]
    if kind == "JPEG":
        if im.mode == "RGBA":              # JPEG has no transparency: put it on white
            bg = Image.new("RGB", im.size, (255, 255, 255)); bg.paste(im, mask=im.getchannel("A")); im = bg
        kw.update(quality=int(quality), optimize=True, subsampling=0 if quality >= 90 else 2)
    elif kind == "WEBP":
        kw.update(quality=int(quality), method=4)
    im.save(path, kind, **kw)
    return path


def preview_bytes(im):
    """The downscaled copy the window edits live: PNG if it has transparency, else JPEG."""
    p = im.copy()
    p.thumbnail((PREVIEW, PREVIEW), Image.Resampling.LANCZOS)
    b = io.BytesIO()
    if p.mode == "RGBA":
        p.save(b, "PNG", compress_level=1); return b.getvalue(), "image/png"
    p.save(b, "JPEG", quality=92); return b.getvalue(), "image/jpeg"


def default_format(fmt, alpha):
    if fmt == "WEBP": return "webp"
    return "png" if alpha or fmt in ("PNG", "GIF") else "jpg"


# ---------------------------------------------------------------- the open image
def downloads_dir():
    d = os.path.join(os.path.expanduser("~"), "Downloads")
    return d if os.path.isdir(d) else os.path.expanduser("~")


def pictures_dir():
    d = os.path.join(os.path.expanduser("~"), "Pictures")
    return d if os.path.isdir(d) else os.path.expanduser("~")


def config_dir():
    if sys.platform == "win32": base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin": base = os.path.expanduser("~/Library/Application Support")
    else: base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "Imgbake" if sys.platform in ("win32", "darwin") else "imgbake")


class Editor:
    """The open image and everything the page asks of it. One at a time; loads and exports take the lock."""

    def __init__(self, prefs_path=None):
        self.lock = threading.Lock()
        self.img = self.meta = self.path = None
        self.name, self.out_dir, self.n = "", "", 0
        self.preview = None
        self.window = None                 # the pywebview window, when there is one (not in browser mode)
        self.drops = {}                    # file name -> full path, from the window's own drop events
        self.last = None                   # last exported file
        self.prefs_path = prefs_path or os.path.join(config_dir(), "prefs.json")

    def load(self, path, name=None, out_dir=None):
        """Open a file. `name`/`out_dir` are for a copy of a file dropped into browser mode, whose real
        location the browser keeps to itself: its exports go to Downloads."""
        img, meta = load(path)
        prev = preview_bytes(img)
        with self.lock:
            self.img, self.meta, self.preview = img, meta, prev
            self.path = None if name else os.path.abspath(path)
            self.name = name or os.path.basename(path)
            self.out_dir = out_dir or os.path.dirname(os.path.abspath(path))
            self.n += 1; self.last = None
        if not name: self.set_prefs({"dir": self.out_dir})
        return self.info()

    def info(self):
        i = {"loaded": self.img is not None, "mode": "window" if self.window else "browser", "app": FROZEN}
        if self.img is not None:
            alpha = self.img.mode == "RGBA"
            i.update(name=self.name, file=self.path or self.name, W=self.img.width, H=self.img.height, n=self.n,
                     fmt=self.meta["format"], alpha=alpha, exif=bool(self.meta.get("exif")),
                     default_format=default_format(self.meta["format"], alpha))
        return i

    def export(self, req):
        fmt = req.get("format") if req.get("format") in FORMATS else "jpg"
        quality = max(1, min(100, int(req.get("quality", 90))))
        with self.lock:
            if self.img is None: raise ValueError("no image open")
            out = render(self.img, req.get("edit"), float(req.get("scale", 1)))
            stem = os.path.splitext(self.name)[0] + "_edited"
            for d in (self.out_dir, downloads_dir()):    # a read-only folder (camera card, network share) -> Downloads
                try:
                    path = save(out, unique_path(os.path.join(d, stem), FORMATS[fmt][1]), fmt, quality,
                                self.meta, req.get("keep_info", True))
                    break
                except PermissionError:
                    if d == downloads_dir(): raise
            self.last = os.path.abspath(path)
        return {"file": self.last, "name": os.path.basename(path), "W": out.width, "H": out.height,
                "bytes": os.path.getsize(path)}

    def prefs(self):
        try:
            with open(self.prefs_path, encoding="utf-8") as f: return json.load(f)
        except Exception:
            return {}

    def set_prefs(self, new):
        p = {**self.prefs(), **new}
        try:
            os.makedirs(os.path.dirname(self.prefs_path), exist_ok=True)
            with open(self.prefs_path, "w", encoding="utf-8") as f: json.dump(p, f)
        except OSError:
            pass                           # preferences are a nicety
        return p

    def start_dir(self):
        d = self.prefs().get("dir")
        return d if d and os.path.isdir(d) else pictures_dir()

    def pick(self):
        """Native Open dialog: the window's own, or (browser mode) the desktop's. Path, "" if cancelled,
        None if there's no dialog to be had."""
        if self.window:
            exts = ";".join(f"*.{e};*.{e.upper()}" for e in OPEN_EXT)
            r = self.window.create_file_dialog(dialog_type=10, directory=self.start_dir(),
                                               file_types=(f"Images ({exts})", "All files (*.*)"))
            return (r[0] if isinstance(r, (list, tuple)) else r) if r else ""
        return pick_file(self.start_dir())

    def dropped(self, name, wait=1.5):
        """Full path of a file just dropped onto the window (pywebview reports it next to the page's drop event)."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < wait:
            hit = self.drops.pop(name, None)
            if hit and time.monotonic() - hit[1] < 10: return hit[0]
            time.sleep(0.05)
        return None


# ---------------------------------------------------------------- desktop helpers
def system_env():
    """Environment for launching the desktop's own programs: PyInstaller points library and Qt paths at
    its bundle, which breaks them."""
    env = os.environ.copy()
    if FROZEN:
        orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if orig is None: env.pop("LD_LIBRARY_PATH", None)
        else: env["LD_LIBRARY_PATH"] = orig
        for k in ("QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "QTWEBENGINEPROCESS_PATH", "QTWEBENGINE_RESOURCES_PATH"):
            env.pop(k, None)
    return env


def reveal(path):
    """Show a file in the system's file manager."""
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)], env=system_env(),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _linux_dialog(initial_dir):
    """The desktop's own dialog via zenity (GNOME and most others) or kdialog (KDE): Tk's Linux dialog
    looks dated. Returns path, "" if cancelled, or None to fall back to Tk."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")): return None
    start = os.path.join(initial_dir, "") if initial_dir else ""
    pats = " ".join(f"*.{e} *.{e.upper()}" for e in OPEN_EXT)
    tries = []
    if shutil.which("zenity"):
        tries.append(["zenity", "--file-selection", "--title=Open image", f"--filename={start}",
                      f"--file-filter=Images | {pats}", "--file-filter=All files | *"])
    if shutil.which("kdialog"):
        tries.append(["kdialog", "--title", "Open image", "--getopenfilename", start or ".", f"{pats}|Images"])
    for cmd in tries:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, env=system_env())
        except Exception:
            continue
        if r.returncode == 0: return r.stdout.strip()
        if r.returncode == 1: return ""          # both use 1 for Cancel
    return None


def pick_file(initial_dir=""):
    """Native file dialog for browser mode, run in a subprocess so it works from the server thread on
    every OS. Returns path, "" if cancelled, or None if no GUI toolkit is available."""
    if sys.platform.startswith("linux"):
        p = _linux_dialog(initial_dir)
        if p is not None: return p
    me = [sys.executable] if FROZEN else [sys.executable, os.path.abspath(__file__)]
    fd, result = tempfile.mkstemp(suffix=".txt"); os.close(fd)
    try:
        r = subprocess.run([*me, "--pick-file", initial_dir, result], capture_output=True, timeout=1800)
        if r.returncode != 0: return None
        with open(result, encoding="utf-8") as f: return f.read().strip()
    except Exception:
        return None
    finally:
        os.remove(result)


def resource(name):
    """A file shipped next to the script, or inside the packaged app."""
    return os.path.join(getattr(sys, "_MEIPASS", _here), name)


UI_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Imgbake</title>
<link rel="icon" type="image/png" href="/icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#111111;--panel:#1a1a1a;--line:#2e2e2e;--text:#ececec;--dim:#9a9a9a;--acc:#e4e4e4;--warn:#ff7a59;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 Roboto,system-ui,-apple-system,Segoe UI,sans-serif;
     display:grid;grid-template-columns:310px 1fr;height:100vh;overflow:hidden;user-select:none;-webkit-user-select:none}
aside{background:var(--panel);border-right:1px solid var(--line);padding:18px;overflow:auto}
h1{font-size:16px;margin:0 0 8px;display:flex;justify-content:space-between;align-items:center}
h1 span{display:flex;gap:4px}
h1 button{padding:3px 9px;font-size:15px;line-height:1.2}
h2{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin:20px 0 10px;
   display:flex;justify-content:space-between;align-items:center;font-weight:600}
h2 button{font-size:11px;padding:3px 8px;letter-spacing:0;text-transform:none}
.h2r{display:flex;gap:10px;align-items:center}
#v_crop{text-transform:none;letter-spacing:0;font-weight:400;font-size:12px;color:var(--acc);font-variant-numeric:tabular-nums}
.filebar{display:flex;gap:8px;align-items:center}
.file{color:var(--dim);font-size:12px;flex:1;min-width:0}
.file,.file small{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}   /* one line each: wrapping shifts the layout */
.file small{display:block;font-size:11px;opacity:.75;margin-top:1px}
button{background:#242424;color:var(--text);border:1px solid var(--line);border-radius:6px;padding:7px 10px;
       cursor:pointer;font:inherit}
button:hover{border-color:var(--acc)}
button:disabled{opacity:.5;cursor:default}
button:disabled:hover{border-color:var(--line)}
button.primary{background:var(--acc);color:#111111;border:none;font-weight:600;padding:9px 14px}
.ctl{margin-bottom:14px}
.ctl label{display:flex;justify-content:space-between;align-items:center;font-size:13px}
.ctl label span{color:var(--acc);font-variant-numeric:tabular-nums}
.ctl .rst{visibility:hidden;border:none;background:none;padding:0 6px;font-size:11px;color:var(--dim);margin-left:auto}
.ctl .rst:hover{color:var(--text)}
.ctl.mod .rst{visibility:visible}
input[type=range]{width:100%;accent-color:var(--acc)}
input[type=number]{background:#111111;color:var(--text);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font:inherit}
.btnrow{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}
.btnrow button{padding:7px 0;white-space:nowrap}
.btnrow button.on{border-color:var(--acc)}
input.num{width:52px;padding:0 2px;border-color:transparent;background:none;text-align:right;color:var(--acc);
          font-variant-numeric:tabular-nums;-moz-appearance:textfield;appearance:textfield}
input.num::-webkit-inner-spin-button,input.num::-webkit-outer-spin-button{-webkit-appearance:none;margin:0}
input.num:hover,input.num:focus{border-color:var(--line);background:#111111;outline:none}
.note{font-size:11.5px;color:var(--dim)}
.warn{color:var(--warn);margin:2px 0 4px}
.export{border-top:1px solid var(--line);padding-top:14px;margin-top:10px}
.export .primary{width:100%;margin-top:4px}
.export .chk{font-size:13px;display:flex;gap:6px;align-items:center;margin:0 0 10px}
.export .ctl{margin:0 0 10px}
#msg{font-size:12px;color:var(--dim);margin-top:8px;word-break:break-all;user-select:text;-webkit-user-select:text}
#msg.err{color:var(--warn)}
#reveal{margin-top:8px;font-size:12px;padding:5px 9px;display:none}
main{position:relative;min-height:0;min-width:0}
.view{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:40px 12px}   /* equal top/bottom so the image is truly centred */
#stage{position:relative;line-height:0;touch-action:none}
#cv{display:block;background:repeating-conic-gradient(#1d1d1d 0 25%,#262626 0 50%) 0 0/16px 16px}   /* shows through transparency */
#cropbox{position:absolute;border:1.5px solid var(--acc);box-shadow:0 0 0 9999px rgba(0,0,0,.62);pointer-events:none}
#stage{overflow:hidden}
#cropbox .g{position:absolute;inset:0;display:none}
#cropbox .g::before,#cropbox .g::after{content:"";position:absolute}
#cropbox .thirds::before{inset:33.33% 0;border-top:1px solid #ffffff40;border-bottom:1px solid #ffffff40}
#cropbox .thirds::after{inset:0 33.33%;border-left:1px solid #ffffff40;border-right:1px solid #ffffff40}
#cropbox .center::before{left:0;right:0;top:50%;border-top:1px dashed #ffffff80}
#cropbox .center::after{top:0;bottom:0;left:50%;border-left:1px dashed #ffffff80}
#cropbox[data-g=thirds] .thirds,#cropbox[data-g=both] .thirds,
#cropbox[data-g=center] .center,#cropbox[data-g=both] .center{display:block}
#cropbox i{position:absolute;width:14px;height:14px;border:3px solid var(--acc)}
#cropbox .nw{left:-3px;top:-3px;border-right:0;border-bottom:0}#cropbox .ne{right:-3px;top:-3px;border-left:0;border-bottom:0}
#cropbox .sw{left:-3px;bottom:-3px;border-right:0;border-top:0}#cropbox .se{right:-3px;bottom:-3px;border-left:0;border-top:0}
#cropbox.bad{border-color:var(--warn)}#cropbox.bad i{border-color:var(--warn)}
body.result #cropbox,body.comparing #cropbox{display:none}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.seg button{border:none;border-radius:0;padding:4px 9px;font-size:12px;background:transparent;color:var(--dim)}
.seg button+button{border-left:1px solid var(--line)}
.seg button.on{background:var(--acc);color:#111111;font-weight:600}
.seg.wide{display:flex;margin-top:6px}
.seg.wide button{flex:1 1 auto;padding:4px 6px}
.guiderow{display:flex;justify-content:space-between;align-items:center;font-size:13px;margin-top:10px}
.export .guiderow{margin:0 0 10px}
#swap{border:none;background:none;padding:0;font-size:12px;color:var(--dim)}
#swap:hover{color:var(--text)}
.tag{position:absolute;top:8px;left:14px;font-size:12px;color:var(--dim);background:#111111cc;padding:3px 8px;border-radius:4px;z-index:2}
.tools{position:absolute;top:8px;right:14px;display:flex;gap:8px;z-index:2}
.tools .seg{background:#111111cc}
#cmp{font-size:12px;padding:4px 10px;background:#111111cc}
body.comparing #cmp{background:var(--acc);color:#111111;border-color:var(--acc)}
#welcome{position:absolute;inset:0;background:var(--bg);display:flex;flex-direction:column;align-items:center;
         justify-content:center;gap:14px;text-align:center;padding:30px;z-index:5}
#welcome h2{margin:0;font-size:20px;font-weight:600;color:var(--text);text-transform:none;letter-spacing:0}
#welcome p{margin:0;color:var(--dim);max-width:460px}
.wrow{display:flex;gap:10px;justify-content:center;margin-top:4px}
#welcome.loading p,#welcome.loading .wrow{display:none}
#welcome.loading h2{font-size:16px;font-weight:500;display:flex;align-items:center;gap:10px}
#welcome.loading h2::before{content:"";width:14px;height:14px;border-radius:50%;border:2px solid #ffffff30;
                            border-top-color:var(--acc);animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.keys{font-size:12px;color:var(--dim);margin-top:18px;line-height:1.9}
.keys kbd{font:inherit;font-size:11px;border:1px solid var(--line);border-radius:4px;padding:1px 6px;color:var(--text);background:var(--panel)}
body.dropping main::after{content:"Drop to open";position:absolute;inset:12px;border:2px dashed var(--acc);border-radius:10px;
     background:#111111e6;color:var(--acc);font-size:18px;display:flex;align-items:center;justify-content:center;z-index:10}
.ctlwrap.off{opacity:.4;pointer-events:none}
</style></head><body>
<aside>
  <h1>Imgbake<span><button id="undo" title="Undo (Ctrl+Z)" disabled>↶</button><button id="redo" title="Redo (Ctrl+Shift+Z)" disabled>↷</button></span></h1>
  <div class="filebar"><div class="file" id="file">No image open<small>Open one to begin</small></div><button id="open" title="Open an image (Ctrl+O)">Open…</button></div>
  <div class="ctlwrap off" id="ctlwrap" inert>
    <h2>Rotate &amp; flip <button id="o_reset">Reset</button></h2>
    <div class="ctl">
      <div class="btnrow">
        <button id="r_ccw" title="Rotate 90° counter-clockwise">↺ 90°</button>
        <button id="r_cw" title="Rotate 90° clockwise">↻ 90°</button>
        <button id="f_h" title="Flip horizontally (mirror)">⇋ Flip</button>
        <button id="f_v" title="Flip vertically (upside down)">⇵ Flip</button>
      </div>
    </div>
    <div class="ctl" id="c_angle">
      <label title="Tilt to level a horizon. The crop shrinks to leave out the empty corners.">Straighten<button class="rst" id="a_rst" title="Reset to 0°">Reset</button><span><input type="number" class="num" id="n_angle" step="0.1" min="-45" max="45" value="0" title="Exact angle in degrees, positive = clockwise">°</span></label>
      <input type="range" id="s_angle" min="-45" max="45" step="0.1" value="0">
    </div>
    <h2>Crop <span class="h2r"><span id="v_crop"></span><button id="c_reset">Reset</button></span></h2>
    <div class="ctl">
      <label>Aspect ratio<button id="swap" title="Switch between portrait and landscape"></button></label>
      <div class="seg wide" id="ratios"><button data-r="free">Free</button><button data-r="original">Original</button><button data-r="1:1">1:1</button><button data-r="4:3">4:3</button><button data-r="3:2">3:2</button><button data-r="16:9">16:9</button></div>
    </div>
    <div class="ctl">
      <div id="w_crop" class="note warn" style="display:none">⚠ Crop includes empty corners (white in JPG, see-through in PNG/WebP)</div>
      <div class="guiderow">Guides<span class="seg" id="guides"><button data-g="thirds">Thirds</button><button data-g="center">Center</button><button data-g="both">Both</button><button data-g="none">None</button></span></div>
    </div>
    <h2>Adjust <button id="t_reset">Reset</button></h2>
    <div id="ctls"></div>
    <div class="export">
      <div class="guiderow">Format<span class="seg" id="fmts"><button data-f="jpg">JPG</button><button data-f="png">PNG</button><button data-f="webp">WebP</button></span></div>
      <div class="ctl" id="c_q"><label>Quality<span id="v_q">90</span></label><input type="range" id="s_q" min="50" max="100" step="1" value="90"></div>
      <div class="guiderow">Size<span class="seg" id="sizes"><button data-s="1">100%</button><button data-s="0.75">75%</button><button data-s="0.5">50%</button><button data-s="0.25">25%</button></span></div>
      <label class="chk" id="l_info" title="Date taken, camera settings and GPS location stored in the photo"><input type="checkbox" id="info" checked> Keep photo info (date, camera, location)</label>
      <button class="primary" id="exp" title="Save a copy (Ctrl+S). The original is never changed.">Export</button>
      <div id="msg"></div>
      <button id="reveal">Show in folder</button>
    </div>
  </div>
</aside>
<main>
  <div id="welcome">
    <h2 id="wtitle">Open an image</h2>
    <p id="wtext">JPEG, PNG, WebP, TIFF, BMP or GIF, or drag one onto this window.</p>
    <div class="wrow"><button class="primary" id="open1">Open image…</button></div>
    <div class="keys" id="keys"><kbd>Ctrl</kbd> <kbd>Z</kbd> undo &nbsp;·&nbsp; <kbd>R</kbd> rotate &nbsp;·&nbsp; hold <kbd>\</kbd> to compare &nbsp;·&nbsp; <kbd>Ctrl</kbd> <kbd>S</kbd> export</div>
  </div>
  <div class="view" id="view">
    <span class="tag" id="hint"></span>
    <div class="tools"><span class="seg" id="views"><button data-v="crop">Crop</button><button data-v="result">Result</button></span><button id="cmp" title="Hold to see the original (or hold the \ key)">Hold to compare</button></div>
    <div id="stage"><canvas id="cv"></canvas>
      <div id="cropbox" data-g="thirds"><div class="g thirds"></div><div class="g center"></div><i class="nw"></i><i class="ne"></i><i class="sw"></i><i class="se"></i></div></div>
  </div>
</main>
<script>
const TOKEN = new URLSearchParams(location.search).get("t") || "";
async function api(path, body){    // POST when there's a body (object -> JSON, Blob -> raw), else GET
  const opt = {headers: {"X-Token": TOKEN}};
  if (body !== undefined){ opt.method = "POST"; opt.body = body instanceof Blob ? body : JSON.stringify(body); }
  const r = await fetch(path, opt);
  if (!r.ok) throw new Error(r.status + " " + r.statusText);
  return r;
}
const json = async (path, body) => (await api(path, body)).json();
const $ = id => document.getElementById(id);
const clamp = (v,a,b) => Math.min(b, Math.max(a, v));
const rad = d => d*Math.PI/180;
const fmt = v => v > 0 ? "+" + v : String(v);
const mac = /Mac/.test(navigator.platform);

const ADJ = [
 {k:"brightness", name:"Brightness"},
 {k:"contrast", name:"Contrast"},
 {k:"saturation", name:"Saturation"},
 {k:"warmth", name:"Warmth"},
];
const fresh = () => ({turns:0, fh:false, fv:false, angle:0, crop:[0,0,1,1], ratio:"free", portrait:false,
                      adj:{brightness:0, contrast:0, saturation:0, warmth:0}});
let E = fresh();                   // the edit: everything Export applies, in the order render() does it
let INFO = null, loaded = false, OW = 0, OH = 0, W = 0, H = 0, W2 = 0, H2 = 0;
let VIEW = "crop", comparing = false, FMT = "jpg", Q = 90, SCALE = 1, PREFS = {};
let BASE = null, BASEDATA = null;  // the preview copy as loaded, and its pixels
const ADJC = document.createElement("canvas");   // the preview with the adjustments applied

/* ---------- geometry (mirrors render() in Python) ---------- */
function geom(){                   // W,H after the quarter turns; W2,H2 after straightening (expanded)
  [W, H] = E.turns % 2 ? [OH, OW] : [OW, OH];
  const t = rad(E.angle), c = Math.abs(Math.cos(t)), s = Math.abs(Math.sin(t));
  const ex = (W*c + H*s)/2, ey = (W*s + H*c)/2;    // the same rounding as Pillow's rotate(expand=True)
  W2 = E.angle ? Math.ceil(W/2 + ex) - Math.floor(W/2 - ex) : W;
  H2 = E.angle ? Math.ceil(H/2 + ey) - Math.floor(H/2 - ey) : H;
}
function inscribed(){              // largest crop with no empty corners
  const t = rad(E.angle), sa = Math.abs(Math.sin(t)), ca = Math.abs(Math.cos(t));
  const wl = W >= H, lng = wl ? W : H, sht = wl ? H : W;
  let wr, hr;
  if (sht <= 2*sa*ca*lng || Math.abs(sa-ca) < 1e-10){
    const x = 0.5*sht; [wr,hr] = wl ? [x/sa, x/ca] : [x/ca, x/sa];
  } else {
    const c2 = ca*ca - sa*sa; wr = (W*ca - H*sa)/c2; hr = (H*ca - W*sa)/c2;
  }
  const pad = E.angle ? 2 : 0;
  wr = Math.max(1, wr-pad); hr = Math.max(1, hr-pad);
  return [(W2-wr)/2/W2, (H2-hr)/2/H2, (W2+wr)/2/W2, (H2+hr)/2/H2];
}
function ratio(){                  // locked width/height in pixels, or 0 for free
  const b = {original: Math.max(W,H)/Math.min(W,H), "1:1":1, "4:3":4/3, "3:2":3/2, "16:9":16/9}[E.ratio];
  return !b ? 0 : E.portrait ? 1/b : b;
}
function fitCrop(){                // largest centred crop at the locked ratio with no empty corners
  const r = ratio();
  if (!r) return inscribed();
  const t = rad(E.angle), c = Math.abs(Math.cos(t)), s = Math.abs(Math.sin(t)), pad = E.angle ? 2 : 0;
  const h = Math.max(1, Math.min(W/(r*c + s), H/(r*s + c)) - pad), w = h*r;
  return [(W2-w)/2/W2, (H2-h)/2/H2, (W2+w)/2/W2, (H2+h)/2/H2];
}
function cropInside(){
  // map each crop corner back into the un-rotated image and check it lands on real pixels
  const th = -rad(E.angle), c = Math.cos(th), sn = Math.sin(th), tol = 2, C = E.crop;
  return [[C[0],C[1]],[C[2],C[1]],[C[0],C[3]],[C[2],C[3]]].every(([x,y]) => {
    const u = x*W2 - W2/2, v = y*H2 - H2/2;
    const sx = c*u - sn*v + W/2, sy = sn*u + c*v + H/2;
    return sx > -tol && sx < W+tol && sy > -tol && sy < H+tol;
  });
}

/* ---------- drawing ---------- */
function lut(a){                   // mirrors tone_lut() in Python
  const g = 2**(-a.brightness/100), k = 2**(a.contrast/100), w = a.warmth/100;
  return [1 + 0.15*w, 1, 1 - 0.15*w].map(gain => {
    const L = new Uint8Array(256);
    for (let v = 0; v < 256; v++){
      const y = (((v/255)**g - 0.5)*k + 0.5)*gain*255;
      L[v] = y < 0 ? 0 : y > 255 ? 255 : Math.floor(y + 0.5);
    }
    return L;
  });
}
function applyAdj(){
  const g = ADJC.getContext("2d"), a = E.adj;
  g.clearRect(0, 0, ADJC.width, ADJC.height);
  if (!ADJ.some(c => a[c.k])) return g.drawImage(BASE, 0, 0);
  const [LR, LG, LB] = lut(a), s = 1 + a.saturation/100, src = BASEDATA.data;
  const out = g.createImageData(ADJC.width, ADJC.height), d = out.data;
  for (let i = 0; i < src.length; i += 4){
    const r = LR[src[i]], gg = LG[src[i+1]], b = LB[src[i+2]];
    if (s !== 1){
      const y = 0.2126*r + 0.7152*gg + 0.0722*b;
      d[i] = y + s*(r - y); d[i+1] = y + s*(gg - y); d[i+2] = y + s*(b - y);
    } else { d[i] = r; d[i+1] = gg; d[i+2] = b; }
    d[i+3] = src[i+3];
  }
  g.putImageData(out, 0, 0);
}
let raf = 0, needAdj = true;
function draw(adj){                // coalesce redraws to one per frame
  if (adj) needAdj = true;
  if (!raf) raf = requestAnimationFrame(() => {
    raf = 0;
    if (!loaded) return;
    if (needAdj){ applyAdj(); needAdj = false; }
    paint(); sync();
  });
}
function paint(){
  const view = $("view"), cv = $("cv"), result = VIEW === "result" && !comparing;
  const [x0,y0,x1,y1] = result ? E.crop : [0,0,1,1];
  const sw = comparing ? OW : (x1-x0)*W2, sh = comparing ? OH : (y1-y0)*H2;
  const k = Math.min((view.clientWidth - 24)/sw, (view.clientHeight - 80)/sh);
  const dw = Math.max(1, Math.round(sw*k)), dh = Math.max(1, Math.round(sh*k)), r = devicePixelRatio || 1;
  cv.style.width = dw + "px"; cv.style.height = dh + "px";
  cv.width = Math.round(dw*r); cv.height = Math.round(dh*r);
  const g = cv.getContext("2d"), s = k*r;   // canvas pixels per full-resolution pixel
  g.imageSmoothingEnabled = true; g.imageSmoothingQuality = "high";
  if (comparing) return g.drawImage(BASE, 0, 0, cv.width, cv.height);
  // the same order as render(): turns, then flips, then straighten (canvas transforms apply last-first)
  g.translate((0.5 - x0)*W2*s, (0.5 - y0)*H2*s);
  g.rotate(rad(E.angle)); g.scale(E.fh ? -1 : 1, E.fv ? -1 : 1); g.rotate(E.turns*Math.PI/2);
  g.drawImage(ADJC, -OW*s/2, -OH*s/2, OW*s, OH*s);
}
function sync(){                   // every label, button state and readout, from E
  const C = E.crop, b = $("cropbox");
  b.style.left = C[0]*100 + "%"; b.style.top = C[1]*100 + "%";
  b.style.width = (C[2]-C[0])*100 + "%"; b.style.height = (C[3]-C[1])*100 + "%";
  const ok = cropInside(), cw = Math.round(C[2]*W2) - Math.round(C[0]*W2), ch = Math.round(C[3]*H2) - Math.round(C[1]*H2);
  b.classList.toggle("bad", !ok); $("w_crop").style.display = ok ? "none" : "block";
  $("v_crop").textContent = cw + " × " + ch + " px";
  $("s_angle").value = E.angle;
  if (document.activeElement !== $("n_angle")) $("n_angle").value = E.angle.toFixed(1);
  $("c_angle").classList.toggle("mod", E.angle !== 0);
  $("f_h").classList.toggle("on", E.fh); $("f_v").classList.toggle("on", E.fv);
  seg("ratios", "r", E.ratio);
  const r = ratio();
  $("swap").style.visibility = r && Math.abs(r - 1) > 1e-9 ? "visible" : "hidden";
  $("swap").textContent = "⇄ " + (E.portrait ? "Portrait" : "Landscape");
  ADJ.forEach(c => {
    $("s_"+c.k).value = E.adj[c.k]; $("v_"+c.k).textContent = fmt(E.adj[c.k]);
    $("c_"+c.k).classList.toggle("mod", E.adj[c.k] !== 0);
  });
  $("exp").textContent = loaded ? `Export ${Math.max(1, Math.round(cw*SCALE))} × ${Math.max(1, Math.round(ch*SCALE))} px` : "Export";
  $("undo").disabled = hi <= 0; $("redo").disabled = hi >= hist.length - 1;
  $("hint").textContent = VIEW === "result" ? "Result: as it will be exported" :
    "Drag the corners or edges to crop · drag outside the box to start a new one";
}
function seg(id, key, v){ document.querySelectorAll(`#${id} button`).forEach(b => b.classList.toggle("on", b.dataset[key] === String(v))); }

/* ---------- undo / redo: every finished change is a snapshot of E ---------- */
let hist = [], hi = -1;
function commit(){
  const s = JSON.stringify(E);
  if (s === hist[hi]) return draw();
  hist = hist.slice(0, hi + 1); hist.push(s); hi = hist.length - 1;
  draw();
}
function travel(d){
  if (!loaded || hi + d < 0 || hi + d >= hist.length) return;
  hi += d; E = JSON.parse(hist[hi]); geom(); draw(true);
}
$("undo").onclick = () => travel(-1);
$("redo").onclick = () => travel(1);

/* ---------- rotate & flip ---------- */
function turn(d){                  // d = 1 clockwise, -1 counter-clockwise; the crop box turns with the picture
  E.turns = (E.turns + d + 4) % 4;
  [E.fh, E.fv] = [E.fv, E.fh];     // a flip followed by a turn = the turn followed by the other flip
  const [x0,y0,x1,y1] = E.crop;
  E.crop = d > 0 ? [1-y1, x0, 1-y0, x1] : [y0, 1-x1, y1, 1-x0];
  E.portrait = !E.portrait;
  geom(); commit();
}
function flip(h){                  // mirroring also mirrors the straighten angle and the crop box
  const [x0,y0,x1,y1] = E.crop;
  if (h){ E.fh = !E.fh; E.crop = [1-x1, y0, 1-x0, y1]; }
  else { E.fv = !E.fv; E.crop = [x0, 1-y1, x1, 1-y0]; }
  E.angle = -E.angle || 0;
  geom(); commit();
}
function setAngle(v){
  v = Math.round(clamp(+v || 0, -45, 45)*10)/10;
  if (v === E.angle) return;
  E.angle = v; geom(); E.crop = fitCrop(); draw();
}
$("r_ccw").onclick = () => turn(-1);
$("r_cw").onclick = () => turn(1);
$("f_h").onclick = () => flip(true);
$("f_v").onclick = () => flip(false);
$("s_angle").addEventListener("input", e => setAngle(e.target.value));
$("s_angle").addEventListener("change", commit);
$("n_angle").addEventListener("change", e => { setAngle(e.target.value); commit(); });
$("a_rst").onclick = () => { setAngle(0); commit(); };
$("o_reset").onclick = () => {
  E.turns = 0; E.fh = E.fv = false; E.angle = 0; geom();
  E.portrait = H > W; E.crop = fitCrop(); commit();
};

/* ---------- crop ---------- */
document.querySelectorAll("#ratios button").forEach(b => b.onclick = () => {
  E.ratio = b.dataset.r;
  if (E.ratio !== "free"){ E.portrait = H > W; E.crop = fitCrop(); }
  commit();
});
$("swap").onclick = () => { E.portrait = !E.portrait; E.crop = fitCrop(); commit(); };
$("c_reset").onclick = () => { E.ratio = "free"; E.crop = inscribed(); commit(); };
function setGuides(g){
  $("cropbox").dataset.g = g; seg("guides", "g", g); savePrefs({guides: g});
}
document.querySelectorAll("#guides button").forEach(b => b.onclick = () => setGuides(b.dataset.g));

const stage = $("stage"), MIN = 0.03;
const CURSOR = {n:"ns-resize",s:"ns-resize",e:"ew-resize",w:"ew-resize",ne:"nesw-resize",sw:"nesw-resize",
                nw:"nwse-resize",se:"nwse-resize",move:"move","":"crosshair"};
let drag = null;
function rel(e){ const r = stage.getBoundingClientRect();
  return {x:(e.clientX-r.left)/r.width, y:(e.clientY-r.top)/r.height, w:r.width, h:r.height}; }
function hit(p){
  const tx = 10/p.w, ty = 10/p.h, [x0,y0,x1,y1] = E.crop;
  const inX = p.x > x0-tx && p.x < x1+tx, inY = p.y > y0-ty && p.y < y1+ty;
  let m = "";
  if (inX && Math.abs(p.y-y0) < ty) m += "n"; else if (inX && Math.abs(p.y-y1) < ty) m += "s";
  if (inY && Math.abs(p.x-x0) < tx) m += "w"; else if (inY && Math.abs(p.x-x1) < tx) m += "e";
  if (!m && p.x > x0 && p.x < x1 && p.y > y0 && p.y < y1) m = "move";
  return m;
}
function freeResize(m, [x0,y0,x1,y1], dx, dy){
  if (m.includes("n")) y0 = clamp(y0+dy, 0, y1-MIN);
  if (m.includes("s")) y1 = clamp(y1+dy, y0+MIN, 1);
  if (m.includes("w")) x0 = clamp(x0+dx, 0, x1-MIN);
  if (m.includes("e")) x1 = clamp(x1+dx, x0+MIN, 1);
  return [x0,y0,x1,y1];
}
function ratioResize(m, [x0,y0,x1,y1], p, r){
  // the opposite corner (or, dragging an edge, the opposite edge's middle) stays put
  const ax = m.includes("w") ? x1 : m.includes("e") ? x0 : (x0+x1)/2;
  const ay = m.includes("n") ? y1 : m.includes("s") ? y0 : (y0+y1)/2;
  const sx = /[ew]/.test(m), sy = /[ns]/.test(m);
  let w = Math.abs(p.x - ax)*W2, h = Math.abs(p.y - ay)*H2;
  if (!sx) w = h*r; else if (!sy) h = w/r; else if (w/h > r) w = h*r; else h = w/r;
  const roomX = m.includes("w") ? ax : m.includes("e") ? 1-ax : 2*Math.min(ax, 1-ax);
  const roomY = m.includes("n") ? ay : m.includes("s") ? 1-ay : 2*Math.min(ay, 1-ay);
  let k = Math.min(roomX*W2/w, roomY*H2/h); k = Math.max(Math.min(1, k), MIN*W2/w, MIN*H2/h);
  const fw = Math.min(1, w*k/W2), fh = Math.min(1, h*k/H2);
  const nx = clamp(m.includes("w") ? ax - fw : m.includes("e") ? ax : ax - fw/2, 0, 1-fw);
  const ny = clamp(m.includes("n") ? ay - fh : m.includes("s") ? ay : ay - fh/2, 0, 1-fh);
  return [nx, ny, nx+fw, ny+fh];
}
stage.addEventListener("pointerdown", e => {
  if (!loaded || VIEW !== "crop" || comparing || e.button !== 0) return;
  const p = rel(e);
  drag = {mode: hit(p) || "new", p0: p, C0: [...E.crop], moved: false};
  stage.setPointerCapture(e.pointerId);
});
stage.addEventListener("pointermove", e => {
  const p = rel(e);
  if (!drag){ stage.style.cursor = VIEW === "crop" ? CURSOR[hit(p)] : "default"; return; }
  const dx = p.x - drag.p0.x, dy = p.y - drag.p0.y;
  if (!drag.moved && Math.hypot(dx*p.w, dy*p.h) < 4) return;
  drag.moved = true;
  const m = drag.mode, r = ratio();
  if (m === "move"){
    const [x0,y0,x1,y1] = drag.C0, w = x1-x0, h = y1-y0, nx = clamp(x0+dx, 0, 1-w), ny = clamp(y0+dy, 0, 1-h);
    E.crop = [nx, ny, nx+w, ny+h];
  } else if (m === "new"){         // draw a fresh box from where the drag started
    const q = {x: clamp(p.x, 0, 1), y: clamp(p.y, 0, 1)}, a = drag.p0;
    const mm = (q.y < a.y ? "n" : "s") + (q.x < a.x ? "w" : "e");
    E.crop = r ? ratioResize(mm, [a.x, a.y, a.x, a.y], q, r) :
      [Math.min(a.x, q.x), Math.min(a.y, q.y), Math.max(a.x, q.x), Math.max(a.y, q.y)].map(v => clamp(v, 0, 1));
  } else {
    E.crop = r ? ratioResize(m, drag.C0, p, r) : freeResize(m, drag.C0, dx, dy);
  }
  draw();
});
const endDrag = () => {
  if (!drag) return;
  const [x0,y0,x1,y1] = E.crop;
  if (drag.mode === "new" && (x1-x0 < MIN || y1-y0 < MIN)) E.crop = drag.C0;   // too small: a stray click
  if (drag.moved) commit();
  drag = null;
};
stage.addEventListener("pointerup", endDrag); stage.addEventListener("pointercancel", endDrag);

/* ---------- adjustments ---------- */
function buildControls(){
  $("ctls").innerHTML = ADJ.map(c => `<div class="ctl" id="c_${c.k}"><label>${c.name}<button class="rst" id="r_${c.k}"
    title="Reset to 0">Reset</button><span id="v_${c.k}">0</span></label>
    <input type="range" id="s_${c.k}" min="-100" max="100" step="1" value="0"></div>`).join("");
  ADJ.forEach(c => {
    $("s_"+c.k).addEventListener("input", e => { E.adj[c.k] = +e.target.value; draw(true); });
    $("s_"+c.k).addEventListener("change", commit);
    $("s_"+c.k).addEventListener("dblclick", () => { E.adj[c.k] = 0; draw(true); commit(); });
    $("r_"+c.k).onclick = () => { E.adj[c.k] = 0; draw(true); commit(); };
  });
}
buildControls();
$("t_reset").onclick = () => { ADJ.forEach(c => E.adj[c.k] = 0); draw(true); commit(); };

/* ---------- view: crop / result, hold to compare ---------- */
function setView(v){ VIEW = v; seg("views", "v", v); document.body.classList.toggle("result", v === "result"); draw(); }
document.querySelectorAll("#views button").forEach(b => b.onclick = () => setView(b.dataset.v));
function compare(on){
  if (!loaded || comparing === on) return;
  comparing = on; document.body.classList.toggle("comparing", on); draw();
}
$("cmp").addEventListener("pointerdown", e => { $("cmp").setPointerCapture(e.pointerId); compare(true); });
["pointerup", "pointercancel"].forEach(t => $("cmp").addEventListener(t, () => compare(false)));
addEventListener("resize", () => draw());

/* ---------- export ---------- */
function setFormat(f){
  FMT = f; seg("fmts", "f", f);
  $("c_q").style.display = f === "png" ? "none" : "";
}
document.querySelectorAll("#fmts button").forEach(b => b.onclick = () => setFormat(b.dataset.f));
$("s_q").addEventListener("input", e => { Q = +e.target.value; $("v_q").textContent = Q; });
$("s_q").addEventListener("change", () => savePrefs({quality: Q}));
function setSize(v){ SCALE = v; seg("sizes", "s", v); savePrefs({size: v}); draw(); }
document.querySelectorAll("#sizes button").forEach(b => b.onclick = () => setSize(+b.dataset.s));
$("info").onchange = () => savePrefs({keep_info: $("info").checked});
function say(text, err){ $("msg").textContent = text; $("msg").classList.toggle("err", !!err); }
const mb = n => n >= 1e6 ? (n/1e6).toFixed(1) + " MB" : Math.max(1, Math.round(n/1e3)) + " KB";
async function exportNow(){
  if (!loaded || $("exp").disabled) return;
  $("exp").disabled = true; $("reveal").style.display = "none"; say("Saving full resolution…");
  try{
    const j = await json("/api/export", {edit: E, format: FMT, quality: Q, scale: SCALE, keep_info: $("info").checked});
    if (j.error) say("Couldn't save: " + j.error, true);
    else { say(`Saved ${j.file} · ${j.W} × ${j.H} px · ${mb(j.bytes)}`); $("reveal").style.display = "inline-block"; }
  } catch(e){ say("Couldn't save: " + e.message, true); }
  $("exp").disabled = false;
}
$("exp").onclick = exportNow;
$("reveal").onclick = () => api("/api/reveal", {});

/* ---------- preferences (kept by the app, so they survive restarts) ---------- */
function savePrefs(p){ Object.assign(PREFS, p); api("/api/prefs", p).catch(() => {}); }

/* ---------- opening images ---------- */
function welcome(title, text, busy){
  $("welcome").style.display = "flex"; $("welcome").classList.toggle("loading", !!busy);
  $("wtitle").textContent = title; if (text !== undefined) $("wtext").textContent = text;
}
async function show(info){
  if (info.error) return welcome("Couldn't open that file", info.error);
  if (info.cancelled) return loaded ? ($("welcome").style.display = "none") : welcome("Open an image");
  if (!info.loaded) return;
  const img = new Image();
  img.src = URL.createObjectURL(await (await api("/api/preview?n=" + info.n)).blob());
  await img.decode();
  BASE = document.createElement("canvas"); BASE.width = ADJC.width = img.naturalWidth; BASE.height = ADJC.height = img.naturalHeight;
  const g = BASE.getContext("2d", {willReadFrequently: true}); g.drawImage(img, 0, 0);
  BASEDATA = g.getImageData(0, 0, BASE.width, BASE.height); URL.revokeObjectURL(img.src);
  INFO = info; OW = info.W; OH = info.H; E = fresh(); geom(); E.portrait = H > W;
  hist = [JSON.stringify(E)]; hi = 0; loaded = true;
  $("file").textContent = ""; $("file").title = info.file;
  const sub = document.createElement("small"); sub.textContent = `${OW} × ${OH} px` + (info.fmt ? " · " + info.fmt : "");
  $("file").append(info.name, sub);
  setFormat(info.default_format);
  $("l_info").style.display = info.exif ? "" : "none";
  $("ctlwrap").classList.remove("off"); $("ctlwrap").inert = false;
  $("welcome").style.display = "none"; say(""); $("reveal").style.display = "none";
  setView("crop"); draw(true);
}
let opening = false;
async function opener(fn){         // one open at a time; keeps the current image if anything goes wrong
  if (opening) return;
  opening = true; $("open").disabled = $("open1").disabled = true;
  try{ await show(await fn()); }
  catch(e){ welcome("Couldn't open that file", String(e.message || e)); }
  opening = false; $("open").disabled = $("open1").disabled = false;
}
const openDialog = () => opener(async () => {
  const j = await json("/api/open", {});
  if (j.error === "nodialog")
    return {error: "No file picker is available on this computer. Drag the image onto this window instead."};
  if (!j.cancelled && !j.error) welcome("Opening…", undefined, true);
  return j;
});
$("open").onclick = $("open1").onclick = openDialog;

/* ---------- drag and drop ---------- */
const canDrop = e => !opening && [...e.dataTransfer.types].includes("Files");
addEventListener("dragover", e => {
  e.preventDefault();
  const ok = canDrop(e); e.dataTransfer.dropEffect = ok ? "copy" : "none";
  document.body.classList.toggle("dropping", ok);
});
addEventListener("dragleave", e => { if (!e.relatedTarget) document.body.classList.remove("dropping"); });
addEventListener("drop", e => {
  e.preventDefault(); document.body.classList.remove("dropping");
  const f = e.dataTransfer.files[0];
  if (!f || !canDrop(e)) return;
  opener(async () => {
    welcome("Opening " + f.name + "…", undefined, true);
    // the window tells the app where the file is; a browser only hands over its contents
    const j = await json("/api/dropped?name=" + encodeURIComponent(f.name), {});
    return j.upload ? json("/api/upload?name=" + encodeURIComponent(f.name), f) : j;
  });
});

/* ---------- keyboard ---------- */
addEventListener("keydown", e => {
  const mod = mac ? e.metaKey : e.ctrlKey, k = e.key.toLowerCase(), typing = e.target.matches("input[type=number]");
  if (mod && k === "o"){ e.preventDefault(); openDialog(); }
  else if (mod && k === "s"){ e.preventDefault(); exportNow(); }
  else if (mod && (k === "y" || (k === "z" && e.shiftKey))){ if (!typing){ e.preventDefault(); travel(1); } }
  else if (mod && k === "z"){ if (!typing){ e.preventDefault(); travel(-1); } }
  else if (typing || mod || e.altKey || !loaded) return;
  else if (e.key === "\\") compare(true);
  else if (k === "r") turn(e.shiftKey ? -1 : 1);
});
addEventListener("keyup", e => { if (e.key === "\\") compare(false); });
addEventListener("blur", () => compare(false));
if (mac) $("keys").innerHTML = $("keys").innerHTML.replaceAll("Ctrl", "⌘");

/* ---------- start ---------- */
(async () => {
  try{ PREFS = await json("/api/prefs"); } catch(e){}
  setGuides(["thirds","center","both","none"].includes(PREFS.guides) ? PREFS.guides : "thirds");
  setSize([1, 0.75, 0.5, 0.25].includes(PREFS.size) ? PREFS.size : 1);
  Q = clamp(+PREFS.quality || 90, 50, 100); $("s_q").value = Q; $("v_q").textContent = Q;
  $("info").checked = PREFS.keep_info !== false;
  setFormat("jpg"); sync();
  const info = await json("/api/info");
  if (info.mode === "browser"){    // no window to close: tell the app when the tab goes, so it can quit
    setInterval(() => api("/api/ping").catch(() => welcome("Imgbake has closed", "Start it again to keep working.")), 20000);
    addEventListener("pagehide", () => navigator.sendBeacon("/api/bye?t=" + TOKEN));
  }
  if (info.loaded) opener(async () => info);
})();
</script></body></html>"""


# ---------------------------------------------------------------- local server for the page
def serve(ed, port=0):
    """Start the page's server on localhost in a background thread. Every request but the icon needs the
    session's random token, so other web pages can't drive it. Returns (server, url, seen)."""
    token = secrets.token_urlsafe(16)
    seen = {"last": time.monotonic(), "bye": None}
    try:
        with open(resource("icon.png"), "rb") as f: icon = f.read()
    except OSError:
        icon = b""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def send(self, body, ctype, code=200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, obj):
            self.send(json.dumps(obj).encode(), "application/json")

        def route(self):
            u = urlparse(self.path); q = parse_qs(u.query)
            if u.path == "/icon.png":
                self.send(icon, "image/png") if icon else self.send(b"not found", "text/plain", 404)
                return u, q, True
            given = self.headers.get("X-Token") or q.get("t", [""])[0]
            if not secrets.compare_digest(given, token):
                self.send(b"forbidden", "text/plain", 403); return u, q, True
            seen["last"] = time.monotonic(); seen["bye"] = None
            return u, q, False

        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def do_GET(self):
            u, q, done = self.route()
            if done: return
            if u.path == "/": return self.send(UI_HTML.encode(), "text/html; charset=utf-8")
            if u.path == "/api/info": return self.send_json(ed.info())
            if u.path == "/api/prefs": return self.send_json(ed.prefs())
            if u.path == "/api/ping": return self.send_json({"ok": True})
            if u.path == "/api/preview" and ed.preview:
                return self.send(ed.preview[0], ed.preview[1])
            self.send(b"not found", "text/plain", 404)

        def do_POST(self):
            u, q, done = self.route()
            if done: return
            try:
                if u.path == "/api/bye":
                    seen["bye"] = time.monotonic(); return self.send_json({"ok": True})
                if u.path == "/api/prefs":
                    return self.send_json(ed.set_prefs(json.loads(self.body() or b"{}")))
                if u.path == "/api/open":
                    p = ed.pick()
                    if p is None: return self.send_json({"error": "nodialog"})
                    if not p: return self.send_json({"cancelled": True})
                    return self.send_json(ed.load(p))
                if u.path == "/api/dropped":
                    name = q.get("name", [""])[0]
                    p = ed.dropped(name) if ed.window else None
                    return self.send_json(ed.load(p) if p else {"upload": True})
                if u.path == "/api/upload":   # browser mode: the file's contents, its location unknown
                    name = os.path.basename(q.get("name", ["image"])[0]) or "image"
                    fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(name)[1])
                    try:
                        with os.fdopen(fd, "wb") as f:
                            left = int(self.headers.get("Content-Length") or 0)
                            while left > 0:
                                chunk = self.rfile.read(min(left, 1 << 20))
                                if not chunk: break
                                f.write(chunk); left -= len(chunk)
                        return self.send_json(ed.load(tmp, name=name, out_dir=downloads_dir()))
                    finally:
                        os.remove(tmp)        # fully loaded into memory, the copy isn't needed
                if u.path == "/api/export":
                    return self.send_json(ed.export(json.loads(self.body() or b"{}")))
                if u.path == "/api/reveal":
                    if ed.last and os.path.exists(ed.last): reveal(ed.last)
                    return self.send_json({"ok": True})
                self.send(b"not found", "text/plain", 404)
            except Exception as e:
                msg = str(e)
                if "cannot identify image file" in msg: msg = "That doesn't look like an image Imgbake can read."
                self.send_json({"error": msg})

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/?t={token}", seen


# ---------------------------------------------------------------- window / browser
def run_window(ed, url):
    """Show the page in its own window until it's closed. False if this computer can't make one."""
    if FROZEN and sys.platform.startswith("linux"):
        os.environ.setdefault("PYWEBVIEW_GUI", "qt")    # the Linux app bundles Qt, not GTK
    try:
        import webview
        from webview.dom import DOMEventHandler
    except Exception as e:
        print(f"No window toolkit available ({e}), using the browser instead.")
        return False
    width, height = 1400, 960
    try:                                 # leave room for the taskbar / menu bar on smaller screens
        s = webview.screens[0]; width, height = min(width, s.width - 40), min(height, s.height - 80)
    except Exception:
        pass
    win = webview.create_window("Imgbake", url, width=max(900, width), height=max(600, height),
                                min_size=(900, 600), background_color="#111111")

    def on_drop(e):                      # the page sees only the file's name; this is where it lives
        for f in (e.get("dataTransfer") or {}).get("files", []):
            if f.get("pywebviewFullPath"): ed.drops[f.get("name")] = (f["pywebviewFullPath"], time.monotonic())

    def on_loaded():
        try: win.dom.document.events.drop += DOMEventHandler(on_drop, prevent_default=True)
        except Exception: pass           # no full paths: drops fall back to uploading the contents

    win.events.loaded += on_loaded
    ed.window = win
    icon = resource("icon.png")
    try:
        webview.start(icon=icon if os.path.exists(icon) else None)
    except Exception as e:
        ed.window = None
        print(f"Couldn't open a window ({e}), using the browser instead.")
        return False
    return True


def run_browser(url, seen):
    """Fallback without a window: the page in the browser; quit once its tab has been closed a few seconds."""
    print(f"\nImgbake running at {url}")
    print("Leave this running while you use it. Closing the browser tab (or Ctrl+C here) quits.")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        while True:
            time.sleep(1)
            now = time.monotonic()
            # 5 s grace lets a reload reconnect; 10 min covers browsers that throttle background tabs
            if (seen["bye"] and now - seen["bye"] > 5) or now - seen["last"] > 600: return
    except KeyboardInterrupt:
        print("\nstopped")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image", nargs="?", help="image to open")
    p.add_argument("--browser", action="store_true", help="use your web browser instead of a window")
    p.add_argument("--port", type=int, default=0, help="local port for the page (default: any free one)")
    args = p.parse_args()
    ed = Editor()
    if args.image:
        try:
            ed.load(args.image)
        except Exception as e:
            sys.exit(f"Couldn't open {args.image}: {e}")
    srv, url, seen = serve(ed, args.port)
    if args.browser or not run_window(ed, url):
        run_browser(url, seen)
    srv.shutdown()


if __name__ == "__main__":
    main()
