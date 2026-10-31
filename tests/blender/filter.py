# needs-window
# Runs the real GPU filter on tiny images. Needs a window and a working GPU.
import numpy as np

import bpy

SIZE = 128
checks = 0
settings = bpy.context.scene.mm_kuwahara


def check(name, condition):
    global checks
    assert condition, name
    checks += 1
    print("PASS:", name)


def make_source(name, pixels, float_buffer=False, size=(SIZE, SIZE)):
    image = bpy.data.images.new(name, width=size[0], height=size[1], alpha=True, float_buffer=float_buffer)
    image.pixels.foreach_set(np.asarray(pixels, dtype=np.float32).reshape(-1))
    image.update()
    return image


def pixels_of(image):
    values = np.empty(image.size[0] * image.size[1] * 4, dtype=np.float32)
    image.pixels.foreach_get(values)
    return values.reshape(image.size[1], image.size[0], 4)


def byte_exact(values):
    """Pixel values that came from 8-bit bytes (k / 255)."""
    return float(np.abs(values * 255.0 - np.rint(values * 255.0)).max())


def run_filter(source):
    settings.image = source
    settings.resolution = str(SIZE)
    settings.export_resolution = "SAME"
    settings.size = 2
    settings.capture_buffers = True
    result = bpy.ops.image.mm_kuwahara_filter()
    return result, settings.output_image


# Pixel values the filter can represent exactly: whole bytes.
flat = np.empty((SIZE, SIZE, 4), dtype=np.float32)
flat[:] = np.round(np.array([0.5, 0.25, 0.75, 1.0]) * 255) / 255

edge = np.empty((SIZE, SIZE, 4), dtype=np.float32)
edge[:, :SIZE // 2] = np.round(np.array([0.1, 0.1, 0.1, 1.0]) * 255) / 255
edge[:, SIZE // 2:] = np.round(np.array([0.9, 0.9, 0.9, 1.0]) * 255) / 255

rng = np.random.default_rng(3)
noise = (rng.integers(0, 256, (SIZE, SIZE, 4)) / 255.0).astype(np.float32)
noise[..., 3] = 1.0

# --- a flat image stays flat (within the byte truncation Material Maker applies)
source = make_source("flat", flat)
result, output = run_filter(source)
check("flat image: filter finishes", result == {"FINISHED"})
check("flat image: an output image was created", output is not None and output.size[:] == (SIZE, SIZE))
values = pixels_of(output)
check("flat image: output is finite and in range", bool(np.isfinite(values).all() and values.min() >= 0 and values.max() <= 1))
check("flat image: colors stay within 2 bytes of the source",
      float(np.abs(values[..., :3] - flat[..., :3]).max()) <= 2 / 255 + 1e-6)
check("flat image: alpha is opaque", float(values[..., 3].min()) == 1.0)
check("flat image: output values are whole bytes", byte_exact(values) < 1e-3)
check("flat image: the source image is untouched", np.array_equal(pixels_of(source), flat))

# --- a hard edge stays sharp and the flat areas stay put
source = make_source("edge", edge)
result, output = run_filter(source)
check("edge image: filter finishes", result == {"FINISHED"})
values = pixels_of(output)
row = values[SIZE // 2, :, 0]
check("edge image: left area keeps its color", abs(row[SIZE // 4] - edge[0, 0, 0]) <= 2 / 255 + 1e-6)
check("edge image: right area keeps its color", abs(row[3 * SIZE // 4] - edge[0, SIZE - 1, 0]) <= 2 / 255 + 1e-6)
check("edge image: the edge stays sharp (no gray ramp)", row[SIZE // 2 - 1] < 0.3 and row[SIZE // 2] > 0.7)

# --- noise: filtered, deterministic, and made of whole bytes
source = make_source("noise", noise)
result, first = run_filter(source)
check("noise image: filter finishes", result == {"FINISHED"})
first_values = pixels_of(first).copy()
check("noise image: output differs from the source", float(np.abs(first_values - noise).max()) > 4 / 255)
check("noise image: output is finite and in range",
      bool(np.isfinite(first_values).all() and first_values.min() >= 0 and first_values.max() <= 1))
check("noise image: output values are whole bytes", byte_exact(first_values) < 1e-3)
result, second = run_filter(source)
check("noise image: running twice gives identical pixels", np.array_equal(pixels_of(second), first_values))

# --- rejected inputs fail with a clear message and create nothing
before = len(bpy.data.images)
tall = make_source("tall", np.zeros((64, 32, 4), dtype=np.float32), size=(32, 64))
settings.image = tall
try:
    bpy.ops.image.mm_kuwahara_filter()
except RuntimeError as error:
    check("non-square image is rejected with a clear message", "square" in str(error))
else:
    check("non-square image is rejected with a clear message", False)

floating = make_source("floating", np.zeros((SIZE, SIZE, 4), dtype=np.float32), float_buffer=True)
settings.image = floating
try:
    bpy.ops.image.mm_kuwahara_filter()
except RuntimeError as error:
    check("float image is rejected with a clear message", "float" in str(error).lower())
else:
    check("float image is rejected with a clear message", False)

check("rejected inputs create no output image", len(bpy.data.images) == before + 2)

# --- clean up what the test created
settings.output_image = None
settings.image = None
for image in list(bpy.data.images):
    if image.name.startswith(("flat", "edge", "noise", "tall", "floating")):
        image.use_fake_user = False
        bpy.data.images.remove(image)

print(f"\nALL {checks} CHECKS PASSED")
