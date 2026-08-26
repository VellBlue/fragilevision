from __future__ import annotations

from pathlib import Path
import struct
import tempfile
import unittest

from fragilevision.core import image_dimensions, import_directory
from fragilevision.db import Database


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


if __name__ == "__main__":
    unittest.main()
