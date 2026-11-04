# needs-window
# The pointer to the original must survive saving and reopening the .blend file.
import importlib
import os
import tempfile

import numpy as np

import bpy

images_mod = importlib.import_module(os.environ["ADDON_MODULE"] + ".images")
settings = bpy.context.scene.mm_kuwahara
checks = 0


def check(name, condition):
    global checks
    assert condition, name
    checks += 1
    print("PASS:", name)


src = bpy.data.images.new("reload_source", width=256, height=256, alpha=True, float_buffer=False)
src.pixels.foreach_set(np.full(256 * 256 * 4, 0.5, dtype=np.float32))
src.update()
src.use_fake_user = True
settings.image = src
settings.resolution = "256"
settings.export_resolution = "SAME"
settings.preview_size = "128"
assert bpy.ops.image.mm_kuwahara_preview() == {"FINISHED"}
assert bpy.ops.image.mm_kuwahara_filter() == {"FINISHED"}
bpy.data.images["reload_source_MM_Preview"].use_fake_user = True   # unused images are not saved

path = os.path.join(tempfile.mkdtemp(), "links_reload.blend")
bpy.ops.wm.save_as_mainfile(filepath=path)
bpy.ops.wm.open_mainfile(filepath=path)

src = bpy.data.images.get("reload_source")
result = bpy.data.images.get("reload_source_MM_Kuwahara")
preview = bpy.data.images.get("reload_source_MM_Preview")
check("source survived the reload", src is not None)
check("result survived the reload", result is not None)
check("preview survived the reload", preview is not None)
check("result still points at its original", images_mod.original_of(result) == src)
check("preview still points at its original", images_mod.original_of(preview) == src)

src.name = "reload_source_renamed"
check("rename after reload: result still finds the original", images_mod.original_of(result) == src)

print(f"\nALL {checks} CHECKS PASSED")
