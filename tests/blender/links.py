# needs-window
# Result/preview links to their original: rename, delete, name reuse, and the
# preview-resize swap that must keep material nodes pointing at the preview.
import importlib
import os
import types

import numpy as np

import bpy

images_mod = importlib.import_module(os.environ["ADDON_MODULE"] + ".images")
RES = 512
checks = 0
settings = bpy.context.scene.mm_kuwahara


def check(name, condition):
    global checks
    assert condition, name
    checks += 1
    print("PASS:", name)


def previews():
    return [i for i in bpy.data.images if i.get("mm_kuwahara_preview")]


def editor(image):
    return types.SimpleNamespace(space_data=types.SimpleNamespace(image=image), scene=bpy.context.scene)


rng = np.random.default_rng(7)
noise = (rng.integers(0, 256, (RES, RES, 4)) / 255.0).astype(np.float32)
noise[..., 3] = 1.0
source = bpy.data.images.new("link_source", width=RES, height=RES, alpha=True, float_buffer=False)
source.pixels.foreach_set(noise.reshape(-1))
source.update()
settings.image = source
settings.resolution = str(RES)
settings.export_resolution = "SAME"
settings.size = 2

# --- the pointer property really stores an image and returns it
settings.preview_size = "256"
check("preview runs", bpy.ops.image.mm_kuwahara_preview() == {"FINISHED"})
preview = previews()[0]
check("preview has a pointer to its original", preview.get("mm_kuwahara_source_ref") == source)
check("preview is marked as having a pointer", bool(preview.get("mm_kuwahara_has_ref")))
first = preview.as_pointer()

# --- rename the original: same preview is reused, no duplicate
source.name = "link_source_renamed"
check("preview runs after rename", bpy.ops.image.mm_kuwahara_preview() == {"FINISHED"})
check("rename: still exactly one preview", len(previews()) == 1)
check("rename: it is the same preview", previews()[0].as_pointer() == first)

# --- full result of a renamed original: never filtered again
check("filter runs", bpy.ops.image.mm_kuwahara_filter() == {"FINISHED"})
result = settings.output_image
check("result has a pointer to its original", result.get("mm_kuwahara_source_ref") == source)
source.name = "link_source_renamed_again"
check("renamed: result resolves to the original", images_mod.resolve_source(editor(result)) == source)
check("renamed: the original is accepted", images_mod.source_problem(source) is None)

# --- preview resize keeps material nodes working and leaves one preview
mat = bpy.data.materials.new("link_material")
try:
    mat.use_nodes = True
except Exception:
    pass
node = mat.node_tree.nodes.new("ShaderNodeTexImage")
node.image = previews()[0]
name_before = previews()[0].name
settings.preview_size = "512"
check("preview 512 runs", bpy.ops.image.mm_kuwahara_preview() == {"FINISHED"})
check("resize: exactly one preview", len(previews()) == 1)
check("resize: the node was moved to the new preview", node.image is not None and node.image == previews()[0])
check("resize: the node image has the new size", tuple(node.image.size) == (512, 512))
check("resize: the name is kept, no .001", previews()[0].name == name_before)
settings.preview_size = "256"
check("preview back to 256 runs", bpy.ops.image.mm_kuwahara_preview() == {"FINISHED"})
check("resize back: node follows again", node.image == previews()[0] and tuple(node.image.size) == (256, 256))

# --- delete the original: result is rejected, a new image with the same name is not mistaken for it
old_name = source.name
bpy.data.images.remove(source)
settings.image = None
check("deleted: pointer is gone", images_mod.original_of(result) is None)
check("deleted: result is rejected as a source",
      "original image is missing" in (images_mod.source_problem(images_mod.resolve_source(editor(result))) or ""))
impostor = bpy.data.images.new(old_name, width=8, height=8)
check("name reused: impostor is not the original", images_mod.original_of(result) is None)
check("name reused: preview does not attach to the impostor",
      not any(images_mod.is_preview_of(p, impostor) for p in previews()))

# --- clean up
settings.output_image = None
settings.node_image = None
bpy.data.materials.remove(mat)
for image in list(bpy.data.images):
    if image.name.startswith("link_source") or image.get("mm_kuwahara_settings") or image == impostor:
        image.use_fake_user = False
        bpy.data.images.remove(image)

print(f"\nALL {checks} CHECKS PASSED")
