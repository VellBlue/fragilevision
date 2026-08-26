#!/usr/bin/env python3
"""Create a tiny rights-safe geometric dataset using only the standard library."""

from pathlib import Path
import argparse
import json
import struct
import zlib


ROOT = Path(__file__).resolve().parents[1]
W, H = 360, 240


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def write_png(path: Path, pixels: list[list[tuple[int, int, int]]]) -> None:
    raw = b"".join(b"\x00" + bytes(channel for pixel in row for channel in pixel) for row in pixels)
    header = b"\x89PNG\r\n\x1a\n"
    path.write_bytes(header + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def canvas(color):
    return [[color for _ in range(W)] for _ in range(H)]


def rect(image, x0, y0, x1, y1, color):
    for y in range(max(0, y0), min(H, y1)):
        for x in range(max(0, x0), min(W, x1)):
            image[y][x] = color


def circle(image, cx, cy, radius, color):
    for y in range(max(0, cy-radius), min(H, cy+radius)):
        for x in range(max(0, cx-radius), min(W, cx+radius)):
            if (x-cx)**2 + (y-cy)**2 <= radius**2:
                image[y][x] = color


def main():
    parser = argparse.ArgumentParser(description="Create the FragileVision geometric demo dataset")
    parser.add_argument("--output", type=Path, default=ROOT / "demo-images")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    cases = []
    for index in range(12):
        lit = index % 2 == 0
        crowded = index in {2, 3, 6, 7, 10}
        night = index not in {4, 5, 8}
        image = canvas((10, 14, 22) if night else (116, 178, 220))
        rect(image, 0, 185, W, H, (20, 28, 31) if night else (67, 120, 70))
        if lit:
            circle(image, 285 if index % 3 else 70, 55, 20, (255, 221, 92))
            circle(image, 285 if index % 3 else 70, 55, 34, (82, 70, 35))
            circle(image, 285 if index % 3 else 70, 55, 19, (255, 221, 92))
        people = 7 if crowded else 2
        for person in range(people):
            x = 45 + person * (270 // max(1, people-1))
            circle(image, x, 155, 12, (8, 9, 10))
            rect(image, x-10, 166, x+10, 205, (8, 9, 10))
        # Preserve visually equivalent repeated conditions while keeping each case
        # byte-distinct for SHA-256 deduplication (one imperceptible corner pixel).
        image[0][0] = (index, 0, 255 - index)
        filename = f"case-{index+1:02d}.png"
        write_png(output / filename, image)
        cases.append({"file": filename, "light": "yes" if lit else "no",
                      "crowded": "yes" if crowded else "no", "night": "yes" if night else "no"})
    (output / "ground-truth.json").write_text(json.dumps({"cases": cases}, indent=2), encoding="utf-8")
    print(f"Created {len(cases)} demo images in {output}")
    print("Note: repeated visual configurations are intentional; the dataset should trigger "
          "FragileVision's near-duplicate warning.")


if __name__ == "__main__":
    main()
