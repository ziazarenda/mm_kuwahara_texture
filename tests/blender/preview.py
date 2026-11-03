# needs-window
# Runs Preview at several sizes in a row and compares it with the full filter.
# Regression test: switching the preview size used to fail with "expected sequence size".
import numpy as np

import bpy

RES = 512
checks = 0
settings = bpy.context.scene.mm_kuwahara


def check(name, condition):
    global checks
    assert condition, name
    checks += 1
    print("PASS:", name)


def pixels_of(image):
    values = np.empty(image.size[0] * image.size[1] * 4, dtype=np.float32)
    image.pixels.foreach_get(values)
    return values.reshape(image.size[1], image.size[0], 4)


rng = np.random.default_rng(5)
noise = (rng.integers(0, 256, (RES, RES, 4)) / 255.0).astype(np.float32)
noise[..., 3] = 1.0
source = bpy.data.images.new("preview_source", width=RES, height=RES, alpha=True, float_buffer=False)
source.pixels.foreach_set(noise.reshape(-1))
source.update()

settings.image = source
settings.resolution = str(RES)
settings.export_resolution = "SAME"
settings.size = 2


def previews():
    return [image for image in bpy.data.images if image.get("mm_kuwahara_preview")]


# --- every size works, in any order, and leaves exactly one preview image
for size in (256, 128, 512, 256, 128, 128, 512):
    settings.preview_size = str(size)
    result = bpy.ops.image.mm_kuwahara_preview()
    check(f"preview {size} runs", result == {"FINISHED"})
    found = previews()
    check(f"preview {size} has the right size", len(found) == 1 and tuple(found[0].size) == (size, size))

# --- the preview is identical to the middle of the full result
bpy.ops.image.mm_kuwahara_filter()
full = pixels_of(settings.output_image)
for size in (128, 256, 512):
    settings.preview_size = str(size)
    bpy.ops.image.mm_kuwahara_preview()
    start = (RES - size) // 2
    crop = full[start:start + size, start:start + size]
    check(f"preview {size} matches the full result", np.array_equal(pixels_of(previews()[0]), crop))

# --- a preview is never offered as a result for nodes
check("preview is not a node result", not any(
    bpy.context.scene.mm_kuwahara.node_image == image for image in previews()))

# --- clean up what the test created
settings.output_image = None
settings.node_image = None
settings.image = None
for image in list(bpy.data.images):
    if image.name.startswith(("preview_source",)) or image.get("mm_kuwahara_settings"):
        image.use_fake_user = False
        bpy.data.images.remove(image)

print(f"\nALL {checks} CHECKS PASSED")
