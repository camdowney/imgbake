#!/usr/bin/env python3
"""
Tests for imgbake.  Run:  python3 test_imgbake.py [name ...]

They make small synthetic images, so they need no data and take a couple of seconds. They check the
edit pipeline (turns, flips, straighten, crop, resize, adjustments), that exports never overwrite and
keep the colour profile and EXIF, and the page's server end to end.
"""
import os, sys, json, shutil, tempfile, time, urllib.request
from PIL import Image
import imgbake as ib

TMP = tempfile.mkdtemp(prefix="imgbake-test-")


def quadrants(w=40, h=20, mode="RGB"):
    """Red top-left, green top-right, blue bottom-left, white bottom-right: easy to tell where each went."""
    im = Image.new(mode, (w, h))
    for (x, y), c in {(0, 0): (255, 0, 0), (1, 0): (0, 255, 0), (0, 1): (0, 0, 255), (1, 1): (255, 255, 255)}.items():
        im.paste(c + ((255,) if mode == "RGBA" else ()), (x * w // 2, y * h // 2, (x + 1) * w // 2, (y + 1) * h // 2))
    return im


def corner(im, x, y):
    """Colour near a corner: x, y are 0 (left/top) or 1 (right/bottom)."""
    return im.convert("RGB").getpixel((2 if not x else im.width - 3, 2 if not y else im.height - 3))


R, G, B, Wh = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)


def test_turns_and_flips():
    im = quadrants()
    cw = ib.render(im, {"turns": 1})
    assert cw.size == (20, 40), cw.size
    assert [corner(cw, 0, 0), corner(cw, 1, 0), corner(cw, 0, 1), corner(cw, 1, 1)] == [B, R, Wh, G]
    ccw = ib.render(im, {"turns": 3})
    assert [corner(ccw, 0, 0), corner(ccw, 1, 0)] == [G, Wh]
    h = ib.render(im, {"fh": True})
    assert [corner(h, 0, 0), corner(h, 1, 0)] == [G, R]
    v = ib.render(im, {"fv": True})
    assert [corner(v, 0, 0), corner(v, 0, 1)] == [B, R]
    # flips are as seen after the turn: turn, then mirror left-right
    tf = ib.render(im, {"turns": 1, "fh": True})
    assert [corner(tf, 0, 0), corner(tf, 1, 0)] == [R, B]


def test_turn_identity_matches_page():
    """The page swaps flips when it turns (flip then turn = turn then the other flip). Check that holds."""
    im = quadrants()
    a = ib.render(ib.render(im, {"fh": True}), {"turns": 1})    # flip, then turn
    b = ib.render(im, {"turns": 1, "fv": True})                 # what the page stores
    assert a.tobytes() == b.tobytes()


def test_straighten_and_crop():
    im = Image.new("RGB", (400, 300), (200, 100, 50))
    r = ib.render(im, {"angle": 10})
    assert r.mode == "RGBA" and r.width > 400 and r.height > 300            # expanded, see-through corners
    assert r.getpixel((0, 0))[3] == 0
    # a crop well inside the picture comes back opaque
    c = ib.render(im, {"angle": 10, "crop": [0.25, 0.25, 0.75, 0.75]})
    assert c.mode == "RGB", c.mode
    W2, H2 = r.size
    assert abs(c.width - W2 / 2) <= 1 and abs(c.height - H2 / 2) <= 1
    # a plain crop is exact
    p = ib.render(im, {"crop": [0.1, 0.2, 0.6, 0.7]})
    assert p.size == (200, 150), p.size


def test_resize():
    im = quadrants(400, 200)
    assert ib.render(im, {}, 0.5).size == (200, 100)
    assert ib.render(im, {"crop": [0, 0, 0.5, 1]}, 0.25).size == (50, 50)


def test_adjustments():
    im = Image.new("RGB", (8, 8), (100, 120, 140))
    assert ib.render(im, {"adj": {}}).getpixel((0, 0)) == (100, 120, 140)
    assert ib.render(im, {"adj": {"brightness": 50}}).getpixel((0, 0))[0] > 100
    assert ib.render(im, {"adj": {"brightness": -50}}).getpixel((0, 0))[0] < 100
    r, g, b = ib.render(im, {"adj": {"warmth": 60}}).getpixel((0, 0))
    assert r > 100 and g == 120 and b < 140
    grey = ib.render(im, {"adj": {"saturation": -100}}).getpixel((0, 0))
    assert max(grey) - min(grey) <= 1, grey
    vivid = ib.render(im, {"adj": {"saturation": 100}}).getpixel((0, 0))
    assert vivid[2] - vivid[0] > 40
    # contrast pushes away from mid grey, both ways
    lo, hi = Image.new("RGB", (2, 2), (60,) * 3), Image.new("RGB", (2, 2), (200,) * 3)
    assert ib.render(lo, {"adj": {"contrast": 60}}).getpixel((0, 0))[0] < 60
    assert ib.render(hi, {"adj": {"contrast": 60}}).getpixel((0, 0))[0] > 200
    # transparency survives
    t = Image.new("RGBA", (4, 4), (100, 120, 140, 77))
    assert ib.render(t, {"adj": {"brightness": 30}}).getpixel((0, 0))[3] == 77


def test_lut_is_monotonic_and_clipped():
    for b in (-100, 0, 100):
        for c in (-100, 0, 100):
            L = ib.tone_lut(b, c, 0)
            assert all(0 <= v <= 255 for v in L)
            assert all(L[i] <= L[i + 1] for i in range(255)), (b, c)


def test_load_orientation_and_modes():
    # a photo stored sideways with EXIF "rotate 90 CW to view" opens upright
    p = os.path.join(TMP, "sideways.jpg")
    ex = Image.Exif(); ex[0x0112] = 6
    quadrants(40, 20).save(p, exif=ex.tobytes(), quality=95)
    im, meta = ib.load(p)
    assert im.size == (20, 40) and meta["format"] == "JPEG"
    assert 0x0112 not in Image.Exif().load(meta["exif"]) if meta["exif"] else True
    # 16-bit greyscale
    p16 = os.path.join(TMP, "deep.png")
    Image.new("I;16", (10, 10), 40000).save(p16)
    im, _ = ib.load(p16)
    assert im.mode == "RGB" and abs(im.getpixel((0, 0))[0] - 156) <= 1
    # palette with transparency keeps it; RGBA with nothing see-through drops it
    pp = os.path.join(TMP, "pal.png")
    pal = Image.new("P", (6, 6), 0); pal.putpalette([0, 0, 0, 255, 0, 0]); pal.info["transparency"] = 0; pal.save(pp, transparency=0)
    assert ib.load(pp)[0].mode == "RGBA"
    po = os.path.join(TMP, "opaque.png")
    Image.new("RGBA", (6, 6), (1, 2, 3, 255)).save(po)
    assert ib.load(po)[0].mode == "RGB"


def test_export_keeps_info_and_never_overwrites():
    d = os.path.join(TMP, "export"); os.makedirs(d)
    src = os.path.join(d, "holiday.jpg")
    ex = Image.Exif(); ex[0x010F] = "TestCam"
    icc = b"\x00" * 128 + b"fake-icc"
    Image.new("RGB", (60, 40), (10, 200, 30)).save(src, exif=ex.tobytes(), icc_profile=icc)
    ed = ib.Editor(prefs_path=os.path.join(TMP, "prefs.json"))
    info = ed.load(src)
    assert info["W"] == 60 and info["exif"] and info["default_format"] == "jpg"
    a = ed.export({"edit": {"crop": [0, 0, 0.5, 0.5]}, "format": "jpg", "quality": 90})
    b = ed.export({"edit": {}, "format": "jpg", "keep_info": False})
    assert a["name"] == "holiday_edited.jpg" and b["name"] == "holiday_edited_2.jpg", (a, b)
    assert (a["W"], a["H"]) == (30, 20)
    with Image.open(a["file"]) as im:
        assert im.getexif().get(0x010F) == "TestCam" and im.info.get("icc_profile") == icc
    with Image.open(b["file"]) as im:
        assert not im.getexif().get(0x010F) and im.info.get("icc_profile") == icc
    for f in ("png", "webp"):
        r = ed.export({"edit": {"angle": 5, "crop": [0, 0, 1, 1]}, "format": f})
        with Image.open(r["file"]) as im:
            assert im.format == ib.FORMATS[f][0] and im.mode == "RGBA" and im.getpixel((0, 0))[3] == 0
    # JPEG can't be see-through: empty corners go white
    r = ed.export({"edit": {"angle": 5, "crop": [0, 0, 1, 1]}, "format": "jpg"})
    with Image.open(r["file"]) as im:
        assert im.mode == "RGB" and min(im.getpixel((0, 0))) > 240
    assert ed.prefs()["dir"] == d


def test_server():
    d = os.path.join(TMP, "server"); os.makedirs(d)
    src = os.path.join(d, "pic.png")
    im = quadrants(300, 200, "RGBA"); im.putpixel((0, 0), (0, 0, 0, 0)); im.save(src)
    ed = ib.Editor(prefs_path=os.path.join(TMP, "prefs-server.json"))
    srv, url, seen = ib.serve(ed)
    base, token = url.split("/?t=")

    def call(path, body=None, token=token):
        req = urllib.request.Request(base + path, data=None if body is None else
                                     (body if isinstance(body, bytes) else json.dumps(body).encode()),
                                     headers={"X-Token": token})
        with urllib.request.urlopen(req) as r: return r.read()
    try:
        try:
            call("/api/info", token="wrong"); assert False, "no token should be refused"
        except urllib.error.HTTPError as e:
            assert e.code == 403
        assert b"<title>Imgbake</title>" in urllib.request.urlopen(url).read()
        assert json.loads(call("/api/info")) ["loaded"] is False
        # browser mode: a drop uploads the contents; exports then go to Downloads
        assert json.loads(call("/api/dropped?name=pic.png", {})) == {"upload": True}
        with open(src, "rb") as f: info = json.loads(call("/api/upload?name=pic.png", f.read()))
        assert info["loaded"] and info["W"] == 300 and info["default_format"] == "png" and info["alpha"]
        prev = call(f"/api/preview?n={info['n']}")
        assert prev[:4] == b"\x89PNG"
        ed.load(src)                            # as if opened from disk: exports go next to it
        r = json.loads(call("/api/export", {"edit": {"turns": 1}, "format": "png", "scale": 0.5}))
        assert r["name"] == "pic_edited.png" and (r["W"], r["H"]) == (100, 150), r
        assert os.path.dirname(r["file"]) == d
        bad = json.loads(call("/api/upload?name=notes.txt", b"just text"))
        assert "error" in bad
        call("/api/prefs", {"guides": "both"})
        assert json.loads(call("/api/prefs"))["guides"] == "both"
    finally:
        srv.shutdown()


TESTS = {k[5:]: v for k, v in globals().items() if k.startswith("test_")}

if __name__ == "__main__":
    names = sys.argv[1:] or list(TESTS)
    failed = 0
    try:
        for n in names:
            t0 = time.time()
            try:
                TESTS[n](); print(f"ok    {n}  ({time.time() - t0:.1f}s)")
            except Exception as e:
                failed += 1; print(f"FAIL  {n}: {type(e).__name__}: {e}")
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(names) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
