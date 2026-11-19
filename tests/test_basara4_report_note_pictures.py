"""The Report asks what the user saw and pressed (optional, typed text that goes into the report after it is hidden
like every other text), and the F11 pictures are compressed inside the zip so that several of them fit into the
size budget. Files in user\\captures are never touched. No window of the real launcher stays open: the dialogs are
driven from the test, and Explorer and the browser are replaced."""
import collections
import hashlib
import io
import json
import os
import random
import struct
import tempfile
import time
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest import mock

from PIL import Image, ImageChops

from launcher_fixtures import bl
from test_basara4_launcher_window import launcher

rp = bl.basara4_report
tk, ttk = bl.tk, bl.ttk


# ---------------------------------------------------------------- pictures

def png_bytes(width=640, height=360, seed=1, alpha=255, text_chunk=None, noise=2):
    """A picture like the game's capture: RGBA, filter 0 on every row, plain zlib, a gradient with a little noise
    (the dithering of a real frame), and nothing but IHDR, IDAT and IEND unless text_chunk is given."""
    rng = random.Random(seed)
    rows = []
    for y in range(height):
        row = bytearray(b"\x00")
        for x in range(width):
            base = (x * 255 // width, y * 255 // height, (x + y) * 255 // (width + height))
            for v in base:
                row.append(max(0, min(255, v + rng.randint(-noise, noise))))
            row.append(alpha(x, y) if callable(alpha) else alpha)
        rows.append(bytes(row))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    if text_chunk:
        out += chunk(b"tEXt", text_chunk)
    return out + chunk(b"IDAT", zlib.compress(b"".join(rows), 6)) + chunk(b"IEND", b"")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def zip_budget(*members):
    """A max_total_bytes that leaves room for exactly these members (name, bytes) and the report texts."""
    cost = sum(size + 2 * len(name) + rp.ZIP_ENTRY_OVERHEAD for name, size in members)
    return rp.ZIP_NOTE_RESERVE + 2 * rp.ZIP_ENTRY_OVERHEAD + 2500 + cost


def report_txt(zf):
    return zf.read("report.txt").decode("utf-8").replace("\r\n", "\n")


def decoded(zf, name):
    return Image.open(io.BytesIO(zf.read(name)))


def biggest_change(a, b):
    return max(hi for _lo, hi in ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getextrema())


class PictureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        patcher = mock.patch.object(rp, "PICTURE_MIN_BYTES", 1000)  # small test pictures count as big ones
        patcher.start()
        self.addCleanup(patcher.stop)

    def pictures(self, count, **kw):
        paths = []
        for n in range(count):
            path = self.dir / f"frame_{n}.png"
            path.write_bytes(png_bytes(seed=n + 1, **kw))
            paths.append((f"captures/frame_{n}.png", path))
        return paths

    def zip_up(self, binaries, budget, **kw):
        target = self.dir / "r.zip"
        skipped = rp.make_zip(target, {"schema": 1}, "report\n", rp.Masker(), binaries=binaries,
                              max_total_bytes=budget, **kw)
        return target, skipped

    def test_compress_picture_without_loss_is_pixel_identical_and_smaller(self):
        (name, path), = self.pictures(1)
        data, info = rp.compress_picture(path, 8)
        self.assertLess(len(data), path.stat().st_size * 0.8)
        self.assertEqual(info["method"], "lossless")
        self.assertEqual(info["max_change"], 0)
        self.assertEqual(biggest_change(Image.open(io.BytesIO(data)), Image.open(path)), 0)

    def test_every_lower_level_is_smaller_and_changes_no_value_by_more_than_it_says(self):
        (name, path), = self.pictures(1)
        before = len(rp.compress_picture(path, 8)[0])
        for bits in (7, 6, 5):
            data, info = rp.compress_picture(path, bits)
            self.assertLess(len(data), before)
            before = len(data)
            self.assertEqual(info["method"], "near-lossless")
            self.assertEqual(info["bits"], bits)
            change = biggest_change(Image.open(io.BytesIO(data)), Image.open(path))
            self.assertEqual(info["max_change"], (1 << (8 - bits)) - 1)
            self.assertLessEqual(change, info["max_change"])
            self.assertGreater(change, 0)  # it really is not lossless

    def test_black_and_white_stay_black_and_white_at_every_level(self):
        path = self.dir / "bw.png"
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 255))
        img.paste((255, 255, 255, 255), (0, 0, 32, 64))
        img.save(path)
        for bits in (8, 7, 6, 5):
            back = Image.open(io.BytesIO(rp.compress_picture(path, bits)[0])).convert("RGB")
            self.assertEqual(back.getpixel((0, 0)), (255, 255, 255))
            self.assertEqual(back.getpixel((63, 0)), (0, 0, 0))

    def test_two_pictures_fit_without_loss_where_one_did(self):
        pics = self.pictures(3)
        orig = pics[0][1].stat().st_size
        lossless = [len(rp.compress_picture(p, 8)[0]) for _n, p in pics]
        # room for two pictures written anew, and not for two as they are (the old zip held one of them)
        budget = zip_budget((pics[0][0], lossless[0]), (pics[1][0], lossless[1])) + 3000
        self.assertLess(budget, zip_budget((pics[0][0], orig), (pics[1][0], pics[1][1].stat().st_size)))
        self.assertGreater(zip_budget((pics[0][0], lossless[0]), (pics[1][0], lossless[1]),
                                      (pics[2][0], lossless[2])), budget)  # and not for a third
        before = {name: sha(path) for name, path in pics}
        target, skipped = self.zip_up(pics, budget, priority=2)
        with zipfile.ZipFile(target) as zf:
            names = zf.namelist()
            self.assertIn(pics[0][0], names)
            self.assertIn(pics[1][0], names)  # the old code left this one out
            self.assertNotIn(pics[2][0], names)
            for name, path in pics[:2]:
                self.assertEqual(biggest_change(decoded(zf, name), Image.open(path)), 0)
            txt = report_txt(zf)
        self.assertRegex(txt, r"captures/frame_0\.png: lossless, \d+ -> \d+ bytes")
        self.assertIn(f"{orig} ->", txt)
        self.assertLessEqual(target.stat().st_size, budget)
        self.assertEqual({name: sha(path) for name, path in pics}, before)  # the files on the PC are untouched

    def test_more_pictures_fit_with_a_small_change_that_the_report_states(self):
        pics = self.pictures(4)
        lossless = [len(rp.compress_picture(p, 8)[0]) for _n, p in pics]
        near = [len(rp.compress_picture(p, 6)[0]) for _n, p in pics]
        # room for four pictures at 6 bits per channel, not for four without loss
        budget = zip_budget(*[(n, s) for (n, _p), s in zip(pics, near)]) + 40000
        self.assertLess(budget, zip_budget(*[(n, s) for (n, _p), s in zip(pics, lossless)]))
        target, skipped = self.zip_up(pics, budget, priority=4)
        with zipfile.ZipFile(target) as zf:
            for name, path in pics:
                self.assertIn(name, zf.namelist())
            txt = report_txt(zf)
            data = json.loads(zf.read("report.json"))
            changes = [biggest_change(decoded(zf, n), Image.open(p)) for n, p in pics]
        self.assertEqual(len([line for line in txt.splitlines() if "near-lossless" in line]), 4)
        self.assertRegex(txt, r"near-lossless, \d bits per colour channel, no value changed by more than \d+ of 255")
        self.assertIn("finer detail than that is lost", txt)
        stated = {entry["max_change"] for entry in data["pictures"]}
        self.assertEqual(len(stated), 1)
        self.assertLessEqual(max(changes), stated.pop())
        self.assertLessEqual(target.stat().st_size, budget)

    def test_the_same_level_is_used_for_all_the_pictures_that_matter(self):
        pics = self.pictures(3)
        near7 = [len(rp.compress_picture(p, 7)[0]) for _n, p in pics]
        budget = zip_budget(*[(n, s) for (n, _p), s in zip(pics, near7)]) + 20000
        target, _skipped = self.zip_up(pics, budget, priority=3)
        with zipfile.ZipFile(target) as zf:
            data = json.loads(zf.read("report.json"))
        self.assertEqual(len(data["pictures"]), 3)
        self.assertEqual(len({(e["method"], e["bits"]) for e in data["pictures"]}), 1)

    def test_a_picture_that_does_not_fit_at_the_lowest_level_is_left_out_and_listed(self):
        pics = self.pictures(1)
        target, skipped = self.zip_up(pics, zip_budget() + 1000)
        with zipfile.ZipFile(target) as zf:
            self.assertNotIn(pics[0][0], zf.namelist())
        self.assertEqual(len(skipped), 1)
        self.assertIn("captures/frame_0.png", skipped[0])
        self.assertLessEqual(target.stat().st_size, zip_budget() + 1000)

    def test_the_leading_pictures_come_first_and_the_rest_only_if_they_fit(self):
        pics = self.pictures(4)
        lossless = len(rp.compress_picture(pics[0][1], 8)[0])
        budget = zip_budget((pics[0][0], lossless + 20000), (pics[1][0], lossless + 20000))
        target, skipped = self.zip_up(pics, budget, priority=2)
        with zipfile.ZipFile(target) as zf:
            names = zf.namelist()
        self.assertEqual([n for n in names if n.startswith("captures/")], [pics[0][0], pics[1][0]])
        self.assertEqual(len(skipped), 2)

    def spy(self):
        """compress_picture as _pack_binaries calls it, with every call (path, bits) recorded."""
        calls, real = [], rp.compress_picture

        def counting(path, bits=8):
            calls.append((str(path), bits))
            return real(path, bits)
        return calls, mock.patch.object(rp, "compress_picture", counting)

    def test_the_number_of_compressions_in_one_zip_is_limited_and_the_rest_is_stored_as_it_is(self):
        pics = self.pictures(5)
        calls, spy = self.spy()
        with mock.patch.object(rp, "PICTURE_MAX_ENCODES", 3), spy:
            target, _skipped = self.zip_up(pics, 50_000_000, priority=5)
        self.assertEqual(len(calls), 3)  # not one more than the limit
        with zipfile.ZipFile(target) as zf:
            names = zf.namelist()
            txt = report_txt(zf)
            stored = [n for n, p in pics[3:] if zf.read(n) == p.read_bytes()]
        self.assertEqual(len([n for n in names if n.startswith("captures/")]), 5)
        self.assertEqual(len(stored), 2)
        self.assertIn("stored as it is, it could not be compressed: there are too many pictures to compress", txt)

    def test_every_picture_is_compressed_once_per_level_however_many_levels_are_tried(self):
        pics = self.pictures(4)
        near6 = [len(rp.compress_picture(p, 6)[0]) for _n, p in pics]
        budget = zip_budget(*[(n, s) for (n, _p), s in zip(pics, near6)]) + 40000
        calls, spy = self.spy()
        with spy:
            target, _skipped = self.zip_up(pics, budget, priority=4)
        self.assertGreaterEqual(len(calls), 4 * 3)  # levels 8, 7 and 6 were tried for the four pictures
        self.assertEqual(max(collections.Counter(calls).values()), 1)  # none twice (the cache)
        with zipfile.ZipFile(target) as zf:
            self.assertEqual(len(json.loads(zf.read("report.json"))["pictures"]), 4)

    def test_a_picture_with_real_transparency_keeps_its_alpha(self):
        path = self.dir / "alpha.png"
        path.write_bytes(png_bytes(alpha=lambda x, y: 255 if x < 320 else 90))
        data, info = rp.compress_picture(path, 8)
        back = Image.open(io.BytesIO(data))
        self.assertEqual(back.mode, "RGBA")
        self.assertFalse(info["alpha_dropped"])
        self.assertEqual(ImageChops.difference(back, Image.open(path)).getbbox(), None)
        opaque, info = rp.compress_picture(self.pictures(1)[0][1], 8)
        self.assertTrue(info["alpha_dropped"])
        self.assertEqual(Image.open(io.BytesIO(opaque)).mode, "RGB")

    def test_a_picture_that_is_not_a_picture_is_stored_as_it_is_and_the_report_says_why(self):
        junk = self.dir / "junk.png"
        junk.write_bytes(os.urandom(5000))
        target, skipped = self.zip_up([("captures/junk.png", junk)], 1_000_000)
        with zipfile.ZipFile(target) as zf:
            self.assertEqual(zf.read("captures/junk.png"), junk.read_bytes())
            txt = report_txt(zf)
        self.assertRegex(txt, r"captures/junk\.png: stored as it is, it could not be compressed: .+")
        self.assertEqual(skipped, [])

    def test_without_pillow_the_pictures_are_stored_as_they_are_and_the_report_says_so(self):
        pics = self.pictures(1)
        with mock.patch.object(rp, "_load_pillow", side_effect=rp.PictureError("Pillow is not available")):
            target, _skipped = self.zip_up(pics, 5_000_000)
        with zipfile.ZipFile(target) as zf:
            self.assertEqual(zf.read(pics[0][0]), pics[0][1].read_bytes())
            self.assertIn("Pillow is not available", report_txt(zf))

    def test_a_picture_too_large_to_decode_safely_is_stored_as_it_is(self):
        pics = self.pictures(1)
        with mock.patch.object(rp, "PICTURE_MAX_PIXELS", 1000):
            target, _skipped = self.zip_up(pics, 5_000_000)
        with zipfile.ZipFile(target) as zf:
            self.assertEqual(zf.read(pics[0][0]), pics[0][1].read_bytes())
            self.assertIn("too many pixels", report_txt(zf))

    def test_metadata_chunks_are_not_copied_into_the_zip(self):
        path = self.dir / "meta.png"
        path.write_bytes(png_bytes(text_chunk=b"Comment\x00written by SECRETNAME9z"))
        self.assertIn(b"SECRETNAME9z", path.read_bytes())
        target, _skipped = self.zip_up([("captures/meta.png", path)], 5_000_000)
        with zipfile.ZipFile(target) as zf:
            self.assertNotIn(b"SECRETNAME9z", zf.read("captures/meta.png"))
            self.assertIn("PNG metadata is not copied", report_txt(zf))

    def test_a_picture_under_the_minimum_is_left_alone(self):
        small = self.dir / "small.png"
        small.write_bytes(png_bytes(width=16, height=16))
        with mock.patch.object(rp, "PICTURE_MIN_BYTES", 512 * 1024):
            target, _skipped = self.zip_up([("captures/small.png", small)], 5_000_000)
        with zipfile.ZipFile(target) as zf:
            self.assertEqual(zf.read("captures/small.png"), small.read_bytes())
            self.assertNotIn("small.png: lossless", report_txt(zf))

    def test_nothing_is_said_about_pictures_when_there_are_none(self):
        target, _skipped = self.zip_up([], 1_000_000)
        with zipfile.ZipFile(target) as zf:
            self.assertNotIn("Pictures", report_txt(zf))
            self.assertNotIn("pictures", json.loads(zf.read("report.json")))


class PictureDefaultsTests(unittest.TestCase):
    """The real constants and the real import of Pillow, which the other picture tests replace."""

    def test_a_small_picture_is_left_alone_with_the_real_minimum(self):
        with tempfile.TemporaryDirectory() as tmp:
            small = Path(tmp) / "small.png"
            small.write_bytes(png_bytes(width=16, height=16))
            self.assertLess(small.stat().st_size, rp.PICTURE_MIN_BYTES)
            target = Path(tmp) / "r.zip"
            rp.make_zip(target, {"schema": 1}, "r\n", rp.Masker(), binaries=[("captures/small.png", small)],
                        max_total_bytes=5_000_000)
            with zipfile.ZipFile(target) as zf:
                self.assertEqual(zf.read("captures/small.png"), small.read_bytes())
                self.assertNotIn("lossless", report_txt(zf))

    def test_when_pillow_cannot_be_imported_the_error_says_so(self):
        import builtins
        real_import = builtins.__import__

        def no_pil(name, *args, **kw):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("no PIL here")
            return real_import(name, *args, **kw)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            path.write_bytes(png_bytes(width=16, height=16))
            with mock.patch("builtins.__import__", no_pil), self.assertRaises(rp.PictureError) as raised:
                rp.compress_picture(path, 8)
            self.assertIn("Pillow is not available", str(raised.exception))
            self.assertIn("no PIL here", str(raised.exception))


class PictureSessionTests(unittest.TestCase):
    """F11 pictures of a real session through send_report: pairs with their text, F11 first, within the budget."""

    def session_with(self, app, game, f11=3, autos=0):
        app.cfg.update(render_trace=True, audio_log=True)
        app.trace_session = bl.new_trace_session()
        app._start_session_report(game)
        bl.CAPTURES_DIR.mkdir(exist_ok=True)
        lines = []
        for kind, count, first in (("f11", f11, 1), ("auto", autos, 100)):
            for n in range(count):
                stem = f"frame_{kind}_{n}"
                (bl.CAPTURES_DIR / f"{stem}.png").write_bytes(png_bytes(seed=first + n))
                (bl.CAPTURES_DIR / f"{stem}.txt").write_text(f"state of {stem}\n", encoding="utf-8")
                reason = "hotkey_f11" if kind == "f11" else "auto_startup"
                lines.append(f"[{n} ms] capture.saved reason={reason} trace=C:\\x\\{stem}.txt screenshot=C:\\x\\{stem}.png\n")
        (app.trace_session / "render_trace.log").write_text("".join(lines), encoding="utf-8")
        app._finish_session_report(0)
        app._finish_trace_session()

    def test_the_session_tells_the_zip_how_many_of_its_pictures_are_f11(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            self.session_with(app, game, f11=3, autos=2)
            art = bl.session_artifacts(app.session_report)
            self.assertEqual(art.priority, 3)
            self.assertEqual(len(art.binaries), 5)
            self.assertTrue(all("f11" in n for n, _p in art.binaries[:3]))  # the F11 pictures come first

    def test_the_level_is_chosen_for_the_f11_pictures_and_not_for_the_automatic_ones(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(rp, "PICTURE_MIN_BYTES", 1000):
            self.session_with(app, game, f11=3, autos=2)
            def total(bits):
                return sum(len(rp.compress_picture(bl.CAPTURES_DIR / f"frame_f11_{n}.png", bits)[0]) for n in range(3))
            near7, near6, near5 = total(7), total(6), total(5)
            # room for the three F11 pictures at 6 bits per channel, and not for them at 7; and an automatic
            # picture does not fit beside them (at 6 bits, the level the F11 pictures need)
            budget = rp.ZIP_NOTE_RESERVE + 25000 + near6
            self.assertGreater(near7, near6 + 60000)
            self.assertGreater(near6, near5 + 60000)
            with mock.patch.object(rp, "ZIP_BUDGET_BYTES", budget), mock.patch.object(bl.Launcher, "_show_report_saved"):
                target = app.send_report()
            with zipfile.ZipFile(target) as zf:
                pictures = json.loads(zf.read("report.json"))["pictures"]
            f11 = [p for p in pictures if "frame_f11_" in p["file"]]
            self.assertEqual(len(f11), 3)
            self.assertEqual({(p["method"], p["bits"]) for p in f11}, {("near-lossless", 6)})
            self.assertLessEqual(Path(target).stat().st_size, budget)

    def test_three_f11_captures_of_a_session_fit_the_budget_with_their_texts(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(rp, "PICTURE_MIN_BYTES", 1000):
            app.cfg.update(render_trace=True, audio_log=True)
            app.trace_session = bl.new_trace_session()
            app._start_session_report(game)
            bl.CAPTURES_DIR.mkdir(exist_ok=True)
            lines = []
            for n in range(3):
                stem = f"frame_f11_{n}"
                (bl.CAPTURES_DIR / f"{stem}.png").write_bytes(png_bytes(seed=n + 1))
                (bl.CAPTURES_DIR / f"{stem}.txt").write_text(f"state of {stem}\n", encoding="utf-8")
                lines.append(f"[{n} ms] capture.saved reason=hotkey_f11 trace=C:\\x\\{stem}.txt screenshot=C:\\x\\{stem}.png\n")
            (app.trace_session / "render_trace.log").write_text("".join(lines), encoding="utf-8")
            app._finish_session_report(0)
            app._finish_trace_session()
            orig = (bl.CAPTURES_DIR / "frame_f11_0.png").stat().st_size
            near = len(rp.compress_picture(bl.CAPTURES_DIR / "frame_f11_0.png", 6)[0])
            budget = rp.ZIP_NOTE_RESERVE + 120000 + 3 * near + 2 * orig // 3  # one original does not fit with the rest
            self.assertLess(budget, 3 * orig)
            before = {p.name: sha(p) for p in bl.CAPTURES_DIR.iterdir()}
            with mock.patch.object(rp, "ZIP_BUDGET_BYTES", budget), \
                    mock.patch.object(bl.Launcher, "_show_report_saved"):
                target = app.send_report()
            self.assertIsNotNone(target)
            with zipfile.ZipFile(target) as zf:
                names = set(zf.namelist())
                for n in range(3):  # every F11 picture, each with the text it belongs to
                    self.assertIn(f"captures/frame_f11_{n}.png", names)
                    self.assertIn(f"captures/frame_f11_{n}.txt", names)
            self.assertLessEqual(Path(target).stat().st_size, budget)
            self.assertEqual({p.name: sha(p) for p in bl.CAPTURES_DIR.iterdir()}, before)


# ---------------------------------------------------------------- the description

class CleanNoteTests(unittest.TestCase):
    def test_a_note_is_trimmed_and_marked_as_asked(self):
        note = rp.clean_note("  it went black\r\nwhen I pressed F11  ")
        self.assertEqual(note, {"asked": True, "text": "it went black\nwhen I pressed F11", "cut": False,
                                "limit": rp.NOTE_MAX_CHARS})

    def test_control_characters_are_dropped_but_line_breaks_and_tabs_stay(self):
        self.assertEqual(rp.clean_note("a\x00b\x1bc\td\ne")["text"], "abc\td\ne")

    def test_a_long_note_is_cut_at_the_limit_and_says_so(self):
        note = rp.clean_note("x" * (rp.NOTE_MAX_CHARS + 500))
        self.assertEqual(len(note["text"]), rp.NOTE_MAX_CHARS)
        self.assertTrue(note["cut"])
        exact = rp.clean_note("y" * rp.NOTE_MAX_CHARS)
        self.assertFalse(exact["cut"])

    def test_nothing_typed_is_asked_but_empty_and_none_is_not_asked(self):
        self.assertEqual(rp.clean_note("   \n ")["text"], "")
        self.assertTrue(rp.clean_note("")["asked"])
        self.assertEqual(rp.clean_note(None), {"asked": False})

    def test_the_text_is_normalised_so_a_name_written_two_ways_is_one_name(self):
        self.assertEqual(rp.clean_note("Ju\u0308rgen")["text"], "J\u00fcrgen")


class NoteReportTests(unittest.TestCase):
    def zip_of(self, app, **kw):
        with mock.patch.object(bl.Launcher, "_show_report_saved"):
            target = app.send_report(**kw)
        self.assertIsNotNone(target)
        return zipfile.ZipFile(target)

    def test_the_note_is_in_report_txt_and_report_json_in_a_section_of_its_own(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            with self.zip_of(app, description="the screen went black\nI pressed F11 and then ESC") as zf:
                txt = report_txt(zf)
                data = json.loads(zf.read("report.json"))
        self.assertIn("--- What the user wrote (typed by the user, not checked) ---", txt)
        self.assertIn("  the screen went black\n  I pressed F11 and then ESC", txt)
        self.assertEqual(data["user_note"]["text"], "the screen went black\nI pressed F11 and then ESC")
        self.assertTrue(data["user_note"]["asked"])
        # the user's words come before the technical parts, where the team reads first
        self.assertLess(txt.index("What the user wrote"), txt.index("--- Port ---"))

    def test_a_name_typed_in_the_note_never_reaches_the_zip(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.dict(os.environ, {"USERNAME": "QAUSER9z", "COMPUTERNAME": "QAPC9z",
                                             "USERPROFILE": "C:\\Users\\QAUSER9z"}):
            note = ("I am qauser9z on QAPC9z, my files are in C:\\Users\\QAUSER9z\\Desktop and "
                    "D:\\Games\\QAUSER9z\\save.dat")
            with self.zip_of(app, description=note) as zf:
                for name in zf.namelist():
                    text = zf.read(name).decode("utf-8", "replace").lower()
                    for private in ("qauser9z", "qapc9z"):
                        self.assertNotIn(private, text, (name, private))
                txt = report_txt(zf)
        self.assertIn("What the user wrote", txt)
        self.assertIn("<user>", txt)  # still readable as what the user meant

    def test_a_crash_report_says_the_user_was_not_asked(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            with self.zip_of(app) as zf:
                txt = report_txt(zf)
                data = json.loads(zf.read("report.json"))
        self.assertIn("(not asked in this report)", txt)
        self.assertEqual(data["user_note"], {"asked": False})

    def test_an_empty_note_says_it_was_left_empty(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            with self.zip_of(app, description="  ") as zf:
                txt = report_txt(zf)
        self.assertIn("(left empty)", txt)

    def test_a_cut_note_says_it_was_cut(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            with self.zip_of(app, description="w" * 1500) as zf:
                txt = report_txt(zf)
                data = json.loads(zf.read("report.json"))
        self.assertIn(f"(cut at {rp.NOTE_MAX_CHARS} characters)", txt)
        self.assertEqual(len(data["user_note"]["text"]), rp.NOTE_MAX_CHARS)

    def test_a_privacy_stop_names_the_note_and_keeps_it_for_the_next_try(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.messagebox, "showerror") as error, \
                mock.patch.object(rp, "make_zip", side_effect=rp.PrivacyError(
                    "report.txt still shows your account name after it was hidden")), \
                mock.patch.object(bl.Launcher, "_show_report_saved") as dialog:
            self.assertIsNone(app.send_report(description="my text"))
            dialog.assert_not_called()
            title, message = error.call_args.args[:2]
            self.assertEqual(title, "Report not saved")
            self.assertIn("description", message)
            self.assertIn("report.txt still shows your account name", message)  # the error is not swallowed
            self.assertEqual(app.last_description, "my text")

    def test_a_privacy_stop_without_a_note_does_not_blame_the_note(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.messagebox, "showerror") as error, \
                mock.patch.object(rp, "make_zip", side_effect=rp.PrivacyError("x still shows your PC name after it was hidden")):
            self.assertIsNone(app.send_report())
            self.assertNotIn("description", error.call_args.args[1])

    def test_the_crash_windows_save_report_does_not_touch_the_cursor_or_the_layout_timing(self):
        """send_report is also what the crash window's Save report calls; its layout tests depend on its timing, so
        it must not process pending window events or change the cursor (that is the Report button's job)."""
        seen = []
        real = rp.make_zip

        def watching(*args, **kw):
            seen.append(str(app.cget("cursor")))
            return real(*args, **kw)
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            with mock.patch.object(rp, "make_zip", watching), mock.patch.object(bl.Launcher, "_show_report_saved"), \
                    mock.patch.object(bl.Launcher, "update_idletasks") as idle:
                app.send_report()
            self.assertEqual(seen, [""])
            idle.assert_not_called()

    def test_a_saved_report_forgets_the_note_kept_for_the_next_try(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            app.last_description = "old text"
            with self.zip_of(app, description="new text"):
                pass
            self.assertEqual(app.last_description, "")


class NoteDialogTests(unittest.TestCase):
    def drive(self, app, action):
        """Runs the dialog without a human: action(win) is what the user does while the window is open."""
        def wait_window(self_, win):
            action(win)
        return mock.patch.object(bl.Launcher, "wait_window", wait_window)

    def test_the_report_button_asks_first_then_makes_the_report_with_the_text(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.Launcher, "send_report") as send:
            def user(win):
                win.text.insert("1.0", "black screen after I pressed F11")
                win.ok.invoke()
            with self.drive(app, user):
                app.on_report()
            send.assert_called_once_with(description="black screen after I pressed F11")

    def test_cancel_makes_no_report(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.Launcher, "send_report") as send:
            def user(win):
                win.text.insert("1.0", "never mind")
                win.cancel.invoke()
            with self.drive(app, user):
                app.on_report()
            send.assert_not_called()

    def test_closing_the_window_is_a_cancel(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.Launcher, "send_report") as send:
            with self.drive(app, lambda win: win.destroy()):
                app.on_report()
            send.assert_not_called()

    def test_nothing_typed_still_makes_a_report(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.Launcher, "send_report") as send:
            with self.drive(app, lambda win: win.ok.invoke()):
                app.on_report()
            send.assert_called_once_with(description="")

    def test_the_text_is_limited_and_the_counter_says_how_much_is_left(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.Launcher, "send_report") as send:
            seen = {}

            def user(win):
                win.text.insert("1.0", "z" * 1400)
                win.update()
                seen["counter"] = str(win.counter.cget("text"))
                seen["length"] = len(win.text.get("1.0", "end-1c"))
                win.ok.invoke()
            with self.drive(app, user):
                app.on_report()
            self.assertEqual(seen["length"], rp.NOTE_MAX_CHARS)
            self.assertIn(f"{rp.NOTE_MAX_CHARS} / {rp.NOTE_MAX_CHARS}", seen["counter"])
            self.assertEqual(len(send.call_args.kwargs["description"]), rp.NOTE_MAX_CHARS)

    def test_the_window_asks_in_plain_english_and_says_the_names_are_hidden(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.Launcher, "send_report"):
            shown = {}

            def user(win):
                labels = [str(w.cget("text")) for w in walk(win) if isinstance(w, (tk.Label, ttk.Label))]
                shown["labels"] = " | ".join(labels)
                shown["title"] = win.title()
                win.cancel.invoke()
            with self.drive(app, user):
                app.on_report()
        self.assertEqual(shown["title"], "Report")
        self.assertIn("What did you see, and what did you press? (optional)", shown["labels"])
        self.assertIn("account name and PC name are hidden", shown["labels"])
        self.assertTrue(shown["labels"].isascii())

    def test_the_two_buttons_are_the_same_size_and_the_main_window_does_not_grow(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.Launcher, "send_report"):
            app.update()
            main_before = (app.winfo_width(), app.winfo_height(), app.winfo_reqwidth(), app.winfo_reqheight())
            sizes = {}

            def user(win):
                win.update()
                sizes["ok"] = (win.ok.winfo_reqwidth(), win.ok.winfo_reqheight())
                sizes["cancel"] = (win.cancel.winfo_reqwidth(), win.cancel.winfo_reqheight())
                sizes["actual"] = (win.ok.winfo_width(), win.cancel.winfo_width())
                win.cancel.invoke()
            with self.drive(app, user):
                app.on_report()
            app.update()
            self.assertEqual(sizes["ok"], sizes["cancel"])
            self.assertEqual(sizes["actual"][0], sizes["actual"][1])
            self.assertEqual(main_before, (app.winfo_width(), app.winfo_height(), app.winfo_reqwidth(), app.winfo_reqheight()))

    def test_after_a_privacy_stop_the_window_opens_with_the_text_again(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.Launcher, "send_report"):
            app.last_description = "what I typed before"
            got = {}

            def user(win):
                got["text"] = win.text.get("1.0", "end-1c")
                win.cancel.invoke()
            with self.drive(app, user):
                app.on_report()
            self.assertEqual(got["text"], "what I typed before")

    def test_the_busy_cursor_shows_while_the_zip_is_made_and_goes_after_every_ending(self):
        seen = []
        real = rp.make_zip

        def watching(*args, **kw):
            seen.append(str(app.cget("cursor")))
            return real(*args, **kw)
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.Launcher, "_show_report_saved"), mock.patch.object(bl.messagebox, "showerror"):
            with mock.patch.object(rp, "make_zip", watching), self.drive(app, lambda win: win.ok.invoke()):
                app.on_report()
            self.assertEqual(seen, ["watch"])
            self.assertEqual(str(app.cget("cursor")), "")
            with mock.patch.object(rp, "make_zip", side_effect=rp.PrivacyError("x still shows your PC name")), \
                    self.drive(app, lambda win: win.ok.invoke()):
                app.on_report()
            self.assertEqual(str(app.cget("cursor")), "")  # also after a privacy stop
            with mock.patch.object(rp, "make_zip", side_effect=OSError(5, "denied")), \
                    self.drive(app, lambda win: win.ok.invoke()):
                app.on_report()
            self.assertEqual(str(app.cget("cursor")), "")  # and after a write error

    def test_a_second_press_of_report_does_not_open_a_second_window(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _), \
                mock.patch.object(bl.Launcher, "send_report"):
            seen = {}

            def user(win):
                seen["again"] = app.ask_description()
                seen["windows"] = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel) and w.title() == "Report"]
                win.cancel.invoke()
            with self.drive(app, user):
                app.on_report()
            self.assertIsNone(seen["again"])
            self.assertEqual(len(seen["windows"]), 1)

    def test_the_report_button_calls_on_report(self):
        with tempfile.TemporaryDirectory() as tmp, launcher(tmp) as (app, paths, game, _):
            button = next(w for w in walk(app) if isinstance(w, ttk.Button) and str(w.cget("text")) == "Report")
            with mock.patch.object(bl.Launcher, "on_report") as on_report:
                button.invoke()
            on_report.assert_called_once_with()


def walk(w):
    yield w
    for child in w.winfo_children():
        yield from walk(child)


if __name__ == "__main__":
    unittest.main()
