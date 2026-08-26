"""Dataset management and reproducibility primitives."""

from __future__ import annotations

import hashlib
import json
import mimetypes
from pathlib import Path
import re
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
from typing import Any

from .db import Database


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_IMAGE_BYTES = 80 * 1024 * 1024
MAX_IMPORT_FILES = 20_000
MAX_PIXEL_COUNT = 100_000_000
FEATURE_EXTRACTOR_VERSION = "sips-bmp-v2"
HASH_GRID_ROWS, HASH_GRID_COLUMNS = 8, 9
MODEL_INPUT_MAX_EDGE = 2048
MODEL_INPUT_MAX_BYTES = 3 * 1024 * 1024
MODEL_INPUT_JPEG_QUALITY = 82


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return value[:60] or "evaluation"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def image_dimensions(path: Path) -> tuple[int | None, int | None]:
    """Read common raster dimensions without decoding private image pixels."""
    try:
        with path.open("rb") as source:
            head = source.read(32)
            if head.startswith(b"\x89PNG\r\n\x1a\n"):
                return struct.unpack(">II", head[16:24])
            if head[:6] in {b"GIF87a", b"GIF89a"}:
                return struct.unpack("<HH", head[6:10])
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                if head[12:16] == b"VP8X" and len(head) >= 30:
                    return (1 + int.from_bytes(head[24:27], "little"),
                            1 + int.from_bytes(head[27:30], "little"))
                if head[12:16] == b"VP8L" and len(head) >= 25 and head[20] == 0x2F:
                    bits = int.from_bytes(head[21:25], "little")
                    return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
                signature = head.find(b"\x9d\x01\x2a")
                if head[12:16] == b"VP8 " and signature >= 0 and len(head) >= signature + 7:
                    width, height = struct.unpack("<HH", head[signature + 3:signature + 7])
                    return (width & 0x3FFF, height & 0x3FFF)
            if head[:2] == b"\xff\xd8":
                source.seek(2)
                while True:
                    marker_start = source.read(1)
                    if not marker_start:
                        break
                    if marker_start != b"\xff":
                        continue
                    marker = source.read(1)
                    while marker == b"\xff":
                        marker = source.read(1)
                    if marker in {b"\xd8", b"\xd9"}:
                        continue
                    length_raw = source.read(2)
                    if len(length_raw) != 2:
                        break
                    length = struct.unpack(">H", length_raw)[0]
                    if marker and marker[0] in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                                                0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                        frame = source.read(5)
                        if len(frame) == 5:
                            height, width = struct.unpack(">HH", frame[1:5])
                            return width, height
                        break
                    source.seek(max(0, length - 2), 1)
    except (OSError, struct.error):
        pass
    return (None, None)


def _thumbnail_pixels(path: Path, edge: int = 32) -> tuple[int, int, list[tuple[int, int, int]]] | None:
    """Decode a small local BMP thumbnail into top-down RGB rows."""
    if sys.platform != "darwin" or not Path("/usr/bin/sips").is_file():
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="fragilevision-feature-") as directory:
            output = Path(directory) / "sample.bmp"
            result = subprocess.run(
                ["/usr/bin/sips", "-s", "format", "bmp", "-z", str(edge), str(edge), str(path), "--out", str(output)],
                capture_output=True, timeout=20, check=False)
            if result.returncode or not output.is_file():
                return None
            data = output.read_bytes()
        if data[:2] != b"BM" or len(data) < 54:
            return None
        offset = struct.unpack_from("<I", data, 10)[0]
        width, height = struct.unpack_from("<ii", data, 18)
        bpp = struct.unpack_from("<H", data, 28)[0]
        if width <= 0 or height == 0 or bpp not in {24, 32}:
            return None
        height_abs, pixel_bytes = abs(height), bpp // 8
        stride = ((width * pixel_bytes + 3) // 4) * 4
        pixels: list[tuple[int, int, int]] = []
        # A positive BMP height means the rows are stored bottom-up. Reading them
        # in file order would give every image a vertically mirrored hash.
        rows = range(height_abs - 1, -1, -1) if height > 0 else range(height_abs)
        for y in rows:
            row = offset + y * stride
            for x in range(width):
                position = row + x * pixel_bytes
                if position + 2 >= len(data):
                    return None
                blue, green, red = data[position:position + 3]
                pixels.append((red, green, blue))
        return width, height_abs, pixels
    except (OSError, subprocess.SubprocessError, struct.error):
        return None


def perceptual_hash(width: int, height: int, luminance: list[float]) -> str:
    """A 64-bit difference hash, as 16 hex characters.

    The thumbnail is resampled to 8 rows of 9 columns and each pixel compared to
    its right-hand neighbour. Comparing neighbours rather than absolute values is
    what makes the hash survive a change of exposure or of JPEG quality, which is
    exactly what separates two frames of one scene from two different scenes.
    """
    if width < HASH_GRID_COLUMNS or height < HASH_GRID_ROWS:
        return ""
    grid = []
    for row in range(HASH_GRID_ROWS):
        top, bottom = row * height // HASH_GRID_ROWS, (row + 1) * height // HASH_GRID_ROWS
        cells = []
        for column in range(HASH_GRID_COLUMNS):
            left, right = column * width // HASH_GRID_COLUMNS, (column + 1) * width // HASH_GRID_COLUMNS
            block = [luminance[y * width + x] for y in range(top, max(bottom, top + 1))
                     for x in range(left, max(right, left + 1))]
            cells.append(sum(block) / len(block) if block else 0.0)
        grid.append(cells)
    bits = 0
    for row in grid:
        for column in range(HASH_GRID_COLUMNS - 1):
            bits = (bits << 1) | int(row[column] < row[column + 1])
    return f"{bits:016x}"


def hamming_distance(first: str, second: str) -> int:
    """Differing bits between two hex perceptual hashes; 64 when incomparable."""
    if not first or not second or len(first) != len(second):
        return 64
    return (int(first, 16) ^ int(second, 16)).bit_count()


def analyze_image_features(path: Path) -> dict[str, Any]:
    """Extract coarse visual signals and a perceptual hash from a local thumbnail."""
    empty: dict[str, Any] = {"brightness": None, "contrast": None, "edge_density": None,
                             "saturation": None, "phash": None}
    decoded = _thumbnail_pixels(path)
    if not decoded:
        return empty
    width, height, pixels = decoded
    try:
        luminance, saturations = [], []
        for red, green, blue in pixels:
            luminance.append((.2126 * red + .7152 * green + .0722 * blue) / 255)
            maximum, minimum = max(red, green, blue), min(red, green, blue)
            saturations.append((maximum - minimum) / maximum if maximum else 0.0)
        edges = []
        for y in range(height):
            for x in range(width):
                index = y * width + x
                if x + 1 < width:
                    edges.append(abs(luminance[index] - luminance[index + 1]))
                if y + 1 < height:
                    edges.append(abs(luminance[index] - luminance[index + width]))
        return {"brightness": statistics.mean(luminance), "contrast": statistics.pstdev(luminance),
                "edge_density": statistics.mean(edges) if edges else 0.0,
                "saturation": statistics.mean(saturations),
                "phash": perceptual_hash(width, height, luminance)}
    except (statistics.StatisticsError, ValueError):
        return empty


def prepare_model_image(image: dict[str, Any], cache_root: Path) -> dict[str, Any]:
    """Create a deterministic, local, cached model input while preserving the original."""
    source = Path(str(image["stored_path"]))
    if not source.is_file():
        raise ValueError("Immagine originale non disponibile")
    width = int(image.get("width") or 0)
    height = int(image.get("height") or 0)
    mime = str(image.get("mime") or mimetypes.guess_type(source.name)[0] or "application/octet-stream")
    source_size = source.stat().st_size
    needs_proxy = (max(width, height) > MODEL_INPUT_MAX_EDGE or source_size > MODEL_INPUT_MAX_BYTES
                   or mime not in {"image/jpeg", "image/png"})
    if not needs_proxy:
        payload = source.read_bytes()
        return {"bytes": payload, "mime": mime, "width": width, "height": height,
                "sha256": hashlib.sha256(payload).hexdigest(), "preprocess": "original"}

    try:
        from PIL import Image, ImageOps  # type: ignore[import-not-found]
    except ImportError:
        Image = ImageOps = None
    if Image is not None:
        engine = "pillow"
    elif sys.platform == "darwin" and Path("/usr/bin/sips").is_file():
        engine = "sips"
    else:
        raise RuntimeError("Riduzione locale non disponibile: installa Pillow")

    profile = f"fit-{MODEL_INPUT_MAX_EDGE}-jpeg-q{MODEL_INPUT_JPEG_QUALITY}-{engine}-v1"
    digest = str(image.get("sha256") or sha256_file(source))
    cache_root.mkdir(parents=True, exist_ok=True)
    target = cache_root / f"{digest[:40]}-{profile}.jpg"
    if not target.is_file() or target.stat().st_size <= 0:
        with tempfile.NamedTemporaryFile(prefix="fv-model-", suffix=".jpg", dir=cache_root, delete=False) as temporary:
            temporary_path = Path(temporary.name)
        try:
            if engine == "pillow":
                with Image.open(source) as opened:
                    normalized = ImageOps.exif_transpose(opened).convert("RGB")
                    normalized.thumbnail((MODEL_INPUT_MAX_EDGE, MODEL_INPUT_MAX_EDGE), Image.Resampling.LANCZOS)
                    normalized.save(temporary_path, "JPEG", quality=MODEL_INPUT_JPEG_QUALITY,
                                    optimize=True, progressive=False)
            else:
                result = subprocess.run([
                    "/usr/bin/sips", "-s", "format", "jpeg", "-s", "formatOptions",
                    str(MODEL_INPUT_JPEG_QUALITY), "-Z", str(MODEL_INPUT_MAX_EDGE),
                    str(source), "--out", str(temporary_path),
                ], capture_output=True, text=True, timeout=120, check=False)
                if result.returncode:
                    raise RuntimeError("Riduzione locale dell’immagine non riuscita")
            proxy_width, proxy_height = image_dimensions(temporary_path)
            if (not proxy_width or not proxy_height or max(proxy_width, proxy_height) > MODEL_INPUT_MAX_EDGE
                    or temporary_path.stat().st_size <= 0):
                raise RuntimeError("La copia ridotta non è valida")
            temporary_path.replace(target)
        finally:
            temporary_path.unlink(missing_ok=True)

    payload = target.read_bytes()
    proxy_width, proxy_height = image_dimensions(target)
    return {"bytes": payload, "mime": "image/jpeg", "width": proxy_width, "height": proxy_height,
            "sha256": hashlib.sha256(payload).hexdigest(), "preprocess": profile}


def import_image_file(db: Database, data_dir: Path, project_id: int, source: Path,
                      filename: str, source_group: str = "") -> dict[str, Any]:
    """Validate and copy one image into managed local storage."""
    safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    extension = Path(safe_name).suffix.lower()
    if not safe_name or extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("formato file non supportato")
    if not db.row("select id from projects where id=? and deleted_at is null", (project_id,)):
        raise ValueError("Progetto inesistente")
    size = source.stat().st_size
    if size <= 0 or size > MAX_IMAGE_BYTES:
        raise ValueError("dimensione non ammessa")
    width, height = image_dimensions(source)
    if not width or not height:
        raise ValueError("formato immagine non riconosciuto")
    if width * height > MAX_PIXEL_COUNT:
        raise ValueError("risoluzione eccessiva")
    digest = sha256_file(source)
    existing = db.row("select id,filename,sha256,deleted_at from images where project_id=? and sha256=?",
                      (project_id, digest))
    if existing:
        if existing["deleted_at"] is not None:
            db.execute("update images set deleted_at=null,filename=?,source_group=? where id=?",
                       (safe_name[:240], source_group[:120], existing["id"]))
            existing.update({"filename": safe_name[:240], "deleted_at": None})
            return {"status": "restored", "image": existing}
        return {"status": "duplicate", "image": existing}
    stored_extension = extension.replace(".jpeg", ".jpg")
    target_root = data_dir / "projects" / str(project_id) / "images"
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / f"{digest[:24]}{stored_extension}"
    if not target.exists():
        shutil.copyfile(source, target)
    mime = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    image_id = db.execute("""insert into images
        (project_id,sha256,filename,stored_path,source_group,mime,width,height,created_at)
        values(?,?,?,?,?,?,?,?,?)""",
        (project_id, digest, safe_name[:240], str(target), source_group[:120], mime,
         width, height, db.now()))
    return {"status": "imported", "image": {"id": image_id, "filename": safe_name, "sha256": digest}}


def import_directory(db: Database, data_dir: Path, project_id: int, directory: str,
                     source_group: str = "", recursive: bool = True) -> dict[str, Any]:
    source_root = Path(directory).expanduser().resolve()
    if not source_root.is_dir():
        raise ValueError("La cartella indicata non esiste o non è leggibile")
    if not db.row("select id from projects where id=? and deleted_at is null", (project_id,)):
        raise ValueError("Progetto inesistente")
    iterator = source_root.rglob("*") if recursive else source_root.glob("*")
    candidates = sorted(path for path in iterator if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS)
    if len(candidates) > MAX_IMPORT_FILES:
        raise ValueError(f"Importazione limitata a {MAX_IMPORT_FILES} immagini per volta")
    imported, duplicates, rejected = [], 0, []
    for source in candidates:
        try:
            result = import_image_file(db, data_dir, project_id, source, source.name, source_group)
            if result["status"] == "duplicate":
                duplicates += 1
                continue
            imported.append(result["image"])
        except (OSError, ValueError) as error:
            rejected.append({"file": source.name, "reason": str(error)})
    return {"found": len(candidates), "imported": len(imported), "duplicates": duplicates,
            "rejected": rejected[:100], "images": imported}


def evaluation_fingerprint(db: Database, project_id: int) -> str:
    """Hash dataset identity, ground truth and exact prompt variants."""
    images = db.rows("""select sha256,source_group,split from images
        where project_id=? and deleted_at is null order by sha256""", (project_id,))
    # The panel behind a label is part of the evaluation's identity: a verdict one
    # reviewer wrote alone and the same verdict three reviewers reached
    # independently are not the same ground truth, so they must not hash alike.
    annotations = db.rows("""select i.sha256,q.key,a.value,a.agreement,a.label_count from annotations a
        join images i on i.id=a.image_id join questions q on q.id=a.question_id
        where i.project_id=? and i.deleted_at is null order by i.sha256,q.key""", (project_id,))
    variants = db.rows("""select q.key,v.name,v.language,v.text,v.mutation_type,v.canonical
        from variants v join questions q on q.id=v.question_id
        where q.project_id=? order by q.key,v.id""", (project_id,))
    canonical = json.dumps({"images": images, "annotations": annotations, "variants": variants},
                           ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
