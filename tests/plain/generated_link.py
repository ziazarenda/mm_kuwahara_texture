"""Check how a result or preview finds its original image (no Blender needed):
rename, delete, name reuse, old files without a pointer, and the 'original missing' message."""
import types
from pathlib import Path

source = (Path(__file__).resolve().parents[2] / "mm_kuwahara_texture" / "images.py").read_text(encoding="utf-8")
start = source.index("def mark_generated")


class Image(dict):
    """Stand-in for a Blender image. Compares by identity, like a Blender ID (not by content)."""
    def __init__(self, name, **properties):
        super().__init__(properties)
        self.name = name
        self.has_data = True
        self.source = "FILE"
        self.size = (256, 256)
        self.is_float = False
        self.channels = 4
        self.colorspace_settings = types.SimpleNamespace(name="sRGB")

    __eq__ = lambda self, other: self is other
    __hash__ = lambda self: id(self)


images = {}


def make(name, **properties):
    image = Image(name, **properties)
    images[name] = image
    return image


def rename(image, new_name):
    del images[image.name]
    image.name = new_name
    images[new_name] = image


def delete(image, *generated):
    """Blender clears pointers to a deleted image; the add-on's marker must survive that."""
    del images[image.name]
    for other in generated:
        if other.get("mm_kuwahara_source_ref") is image:
            other["mm_kuwahara_source_ref"] = None


bpy = types.SimpleNamespace(data=types.SimpleNamespace(images=types.SimpleNamespace(get=images.get)))
namespace = {"bpy": bpy}
exec(source[start:], namespace)
mark_generated, original_of, is_preview_of = (namespace[n] for n in ("mark_generated", "original_of", "is_preview_of"))
source_problem, resolve_source = namespace["source_problem"], namespace["resolve_source"]


def context(image):
    return types.SimpleNamespace(space_data=types.SimpleNamespace(image=image),
                                 scene=types.SimpleNamespace(mm_kuwahara=types.SimpleNamespace(image=None, output_image=None)))


checks = 0


def check(condition, message):
    global checks
    checks += 1
    assert condition, message


wood = make("Wood")
other = make("Stone")
result = make("Wood_MM_Kuwahara", mm_kuwahara_settings="{}")
preview = make("Wood_MM_Preview", mm_kuwahara_settings="{}", mm_kuwahara_preview=True)
mark_generated(result, wood)
mark_generated(preview, wood)

# --- normal case
check(original_of(result) is wood, "result finds its original")
check(is_preview_of(preview, wood), "preview belongs to its original")
check(not is_preview_of(preview, other), "preview does not belong to another image")
check(not is_preview_of(result, wood), "a result is not a preview")
check(source_problem(wood) is None, "a normal image is accepted")

# --- rename the original: pointer still works
rename(wood, "Wood_renamed")
check(images.get("Wood") is None, "old name is gone")
check(original_of(result) is wood, "renamed: result still finds its original")
check(is_preview_of(preview, wood), "renamed: the same preview is still found (no duplicate)")
check(resolve_source(context(result)) is wood, "renamed: editing a result starts from the original")
check(resolve_source(context(preview)) is wood, "renamed: editing a preview starts from the original")

# --- delete the original
delete(wood, result, preview)
check(original_of(result) is None, "deleted: no original found")
check(resolve_source(context(result)) is result, "deleted: resolve gives back the result itself")
check("original image is missing" in source_problem(result), "deleted: a result is rejected with the 'missing' message")
check("preview" in source_problem(preview), "deleted: a preview is rejected")

# --- a different image takes the old name later: must NOT be mistaken for the original
impostor = make("Wood_renamed")
check(original_of(result) is None, "name reused: the impostor is not the original")
check(not is_preview_of(preview, impostor), "name reused: preview does not attach to the impostor")

# --- files made by older versions (name only, no pointer)
old_result = make("Old_MM_Kuwahara", mm_kuwahara_source="Old", mm_kuwahara_settings="{}")
old = make("Old")
check(original_of(old_result) is old, "old file: found by name")
check(resolve_source(context(old_result)) is old, "old file: resolve goes back to the original")
delete(old)
check(original_of(old_result) is None, "old file: original gone")
check("original image is missing" in source_problem(old_result), "old file: rejected with the 'missing' message")

# --- unrelated images
check(original_of(other) is None, "plain image has no original")
check(resolve_source(context(other)) is other, "plain image is its own source")

# --- channel message is neutral
odd = make("Odd")
odd.channels = 3
check(source_problem(odd) == "Unsupported image channel layout", "odd channel count gets the neutral message")

print(f"generated_link: {checks} checks passed")
