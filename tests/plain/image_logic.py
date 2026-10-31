"""Byte-input rounding and the PNG writer, without Blender. Needs numpy."""
import os
import struct
import sys
import tempfile
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fake_blender import load_addon  # noqa: E402

addon, _ = load_addon(ROOT / "mm_kuwahara_texture" / "__init__.py")
checks = 0


def check(name, condition):
    global checks
    assert condition, name
    checks += 1
    print("ok:", name)


# --- material_maker_byte_input -------------------------------------------
bytes_as_floats = (np.arange(256, dtype=np.float32) / np.float32(255.0)).astype(np.float32)
result = addon.material_maker_byte_input(bytes_as_floats)
check("byte input keeps float32 dtype", result.dtype == np.float32)
check("byte input only clears the low 13 mantissa bits",
      np.array_equal(result.view(np.uint32), bytes_as_floats.view(np.uint32) & np.uint32(0xFFFFE000)))
check("byte input never rounds up", bool((result <= bytes_as_floats).all()))
check("byte input is exactly representable as half precision",
      np.array_equal(result.astype(np.float16).astype(np.float32), result))
check("byte input is idempotent", np.array_equal(addon.material_maker_byte_input(result), result))
check("byte input keeps 0 and 1", result[0] == 0.0 and result[255] == 1.0)
image = np.random.default_rng(1).random((4, 4, 4)).astype(np.float32)
check("byte input keeps the array shape", addon.material_maker_byte_input(image).shape == (4, 4, 4))


# --- PNG writer: decode what it wrote with a small independent decoder ----
def decode_png(path):
    data = Path(path).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    position, chunks, idat = 8, [], b""
    while position < len(data):
        (length,) = struct.unpack(">I", data[position:position + 4])
        kind = data[position + 4:position + 8]
        body = data[position + 8:position + 8 + length]
        (crc,) = struct.unpack(">I", data[position + 8 + length:position + 12 + length])
        assert crc == zlib.crc32(kind + body) & 0xFFFFFFFF, f"bad CRC in {kind}"
        chunks.append(kind)
        if kind == b"IDAT":
            idat += body
        elif kind == b"IHDR":
            width, height, depth, color, *_ = struct.unpack(">IIBBBBB", body)
        position += 12 + length
    assert (depth, color) == (8, 6), "expected 8-bit RGBA"
    raw, stride = zlib.decompress(idat), width * 4
    rows, previous = [], bytearray(stride)
    for y in range(height):
        kind, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - 4] if i >= 4 else 0
            b = previous[i]
            c = previous[i - 4] if i >= 4 else 0
            if kind == 1:
                line[i] = (line[i] + a) & 255
            elif kind == 2:
                line[i] = (line[i] + b) & 255
            elif kind == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif kind == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(bytes(line))
        previous = line
    return chunks, np.frombuffer(b"".join(rows), dtype=np.uint8).reshape(height, width, 4)


with tempfile.TemporaryDirectory() as folder:
    rng = np.random.default_rng(7)
    noise = rng.integers(0, 256, (37, 53, 4), dtype=np.uint8)
    gradient = np.zeros((32, 32, 4), dtype=np.uint8)
    gradient[..., 0] = np.arange(32)[None, :] * 8
    gradient[..., 1] = np.arange(32)[:, None] * 8
    gradient[..., 3] = 255
    flat = np.full((16, 16, 4), 200, dtype=np.uint8)

    for label, pixels in (("noise", noise), ("gradient", gradient), ("flat", flat)):
        for compression in (0, 80, 100):
            target = Path(folder) / f"{label}_{compression}.png"
            addon.write_lossless_rgba_png(target, pixels, compression)
            chunks, decoded = decode_png(target)
            check(f"{label} at compression {compression} decodes to the same pixels",
                  np.array_equal(decoded, pixels))

    chunks, _ = decode_png(Path(folder) / "noise_80.png")
    check("sRGB images carry an sRGB chunk", b"sRGB" in chunks)
    check("chunk order is IHDR first and IEND last", chunks[0] == b"IHDR" and chunks[-1] == b"IEND")

    addon.write_lossless_rgba_png(Path(folder) / "raw.png", flat, 50, srgb=False)
    check("Non-Color images carry no sRGB chunk", b"sRGB" not in decode_png(Path(folder) / "raw.png")[0])

    single = np.array([[[1, 2, 3, 4]]], dtype=np.uint8)
    addon.write_lossless_rgba_png(Path(folder) / "one.png", single, 80)
    check("a 1x1 image works", np.array_equal(decode_png(Path(folder) / "one.png")[1], single))

    existing = Path(folder) / "existing.png"
    existing.write_bytes(b"old")
    addon.write_lossless_rgba_png(existing, flat, 80)
    check("an existing file is replaced", decode_png(existing)[1].shape == (16, 16, 4))
    check("no temporary files are left behind",
          not [name for name in os.listdir(folder) if name.endswith(".tmp")])

    for label, bad, compression in (
        ("float data", flat.astype(np.float32), 80),
        ("three channels", flat[..., :3], 80),
        ("empty image", np.zeros((0, 4, 4), dtype=np.uint8), 80),
        ("compression above 100", flat, 101),
        ("negative compression", flat, -1),
    ):
        target = Path(folder) / "bad.png"
        try:
            addon.write_lossless_rgba_png(target, bad, compression)
        except ValueError:
            check(f"rejects {label}", True)
        else:
            check(f"rejects {label}", False)
        check(f"{label}: nothing written", not target.exists())
    check("a failed write leaves no temporary files",
          not [name for name in os.listdir(folder) if name.endswith(".tmp")])

print(f"\nALL {checks} IMAGE LOGIC CHECKS PASSED")
