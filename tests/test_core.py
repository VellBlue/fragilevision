from __future__ import annotations

from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib

from fragilevision import core
from fragilevision.core import (FEATURE_EXTRACTOR_VERSIONS, feature_engine, feature_extractor_version,
                                image_dimensions, import_directory)
from fragilevision.db import Database


def write_png(path: Path, rows: list[list[tuple[int, int, int]]]) -> None:
    """A minimal RGB PNG, so the tests need no image library to make a fixture."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    height, width = len(rows), len(rows[0])
    raw = b"".join(b"\x00" + bytes(channel for pixel in row for channel in pixel) for row in rows)
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def pillow_available() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


class CoreTests(unittest.TestCase):
    def test_png_dimensions_are_read_without_decoding_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "header.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", 640, 480) + b"\x00" * 8)
            self.assertEqual(image_dimensions(path), (640, 480))

    def test_import_rejects_an_extension_disguised_as_an_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "not-really.png").write_bytes(b"not an image")
            db = Database(root / "data" / "db.sqlite3")
            project_id = db.execute("insert into projects(name,slug,description,created_at) values(?,?,?,?)",
                                    ("Test", "test", "", db.now()))
            result = import_directory(db, root / "data", project_id, str(source))
            self.assertEqual(result["imported"], 0)
            self.assertEqual(len(result["rejected"]), 1)


class FeatureEngineTests(unittest.TestCase):
    """The visual analysis must never fail silently, and never mix two engines."""

    def test_without_a_decoder_there_is_no_engine_and_no_cache_version(self):
        with mock.patch.object(core.sys, "platform", "linux"), mock.patch.dict(sys.modules, {"PIL": None}):
            self.assertIsNone(feature_engine())
            self.assertEqual(feature_extractor_version(), "none")
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "flat.png"
                write_png(path, [[(120, 120, 120)] * 40 for _ in range(40)])
                self.assertIsNone(core._thumbnail_pixels(path))
                self.assertIsNone(core.analyze_image_features(path)["phash"])

    def test_the_two_engines_never_share_a_cache_version(self):
        """Measured: sips and Pillow differ by up to 7 bits on the same photograph."""
        self.assertNotEqual(FEATURE_EXTRACTOR_VERSIONS["sips"], FEATURE_EXTRACTOR_VERSIONS["pillow"])
        self.assertEqual(feature_extractor_version(), FEATURE_EXTRACTOR_VERSIONS[feature_engine()]
                         if feature_engine() else "none")

    def test_sips_bmp_returns_each_pixel_once_in_top_down_order(self):
        """The BMP decoder must not duplicate pixels or leave its rows upside down."""
        # Two 24-bit rows are stored bottom-up and padded to a four-byte boundary.
        # File order: blue/white, then red/green. Expected output is the reverse.
        bottom = bytes((255, 0, 0, 255, 255, 255, 0, 0))
        top = bytes((0, 0, 255, 0, 255, 0, 0, 0))
        pixels = bottom + top
        header = (b"BM" + struct.pack("<IHHI", 54 + len(pixels), 0, 0, 54)
                  + struct.pack("<IiiHHIIiiII", 40, 2, 2, 1, 24, 0, len(pixels), 0, 0, 0, 0))

        def fake_sips(command, **_kwargs):
            Path(command[-1]).write_bytes(header + pixels)
            return SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
                core.subprocess, "run", side_effect=fake_sips):
            decoded = core._thumbnail_pixels_sips(Path(directory) / "source.png", 2)

        self.assertEqual(decoded, (2, 2, [
            (255, 0, 0), (0, 255, 0),
            (0, 0, 255), (255, 255, 255),
        ]))

    @unittest.skipUnless(pillow_available(), "Pillow non installato")
    def test_pillow_decodes_a_square_thumbnail_the_right_way_up(self):
        """A bottom-up read would mirror every hash: the sips path had to guard it too."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "half.png"
            write_png(path, [[(240, 240, 240)] * 64 for _ in range(32)]
                            + [[(10, 10, 10)] * 64 for _ in range(32)])
            decoded = core._thumbnail_pixels_pillow(path, 32)
            self.assertIsNotNone(decoded)
            width, height, pixels = decoded
            self.assertEqual((width, height), (32, 32))
            self.assertEqual(len(pixels), 32 * 32)
            self.assertGreater(pixels[0][0], pixels[-1][0])


if __name__ == "__main__":
    unittest.main()
