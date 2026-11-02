"""Check which image Filter, Preview and Export pick (no Blender needed)."""
import types
from pathlib import Path

source = (Path(__file__).resolve().parents[2] / "mm_kuwahara_texture" / "__init__.py").read_text(encoding="utf-8")
start = source.index("def resolve_source")
end = source.index("def image_rgba")


class Image(dict):
    def __init__(self, name, **properties):
        super().__init__(properties)
        self.name = name


images = {}


def make(name, **properties):
    image = Image(name, **properties)
    images[name] = image
    return image


bpy = types.SimpleNamespace(data=types.SimpleNamespace(images=types.SimpleNamespace(get=images.get)))
namespace = {"bpy": bpy}
exec(source[start:end], namespace)
resolve_source = namespace["resolve_source"]
result_image = namespace["result_image"]


def context(editor_image, pinned=None, last_result=None):
    settings = types.SimpleNamespace(image=pinned, output_image=last_result)
    return types.SimpleNamespace(space_data=types.SimpleNamespace(image=editor_image) if editor_image is not False else None,
                                 scene=types.SimpleNamespace(mm_kuwahara=settings))


checks = 0


def check(actual, expected, message):
    global checks
    checks += 1
    assert actual is expected, f"{message}: got {getattr(actual, 'name', actual)}, expected {getattr(expected, 'name', expected)}"


wood = make("Wood")
other = make("Stone")
result = make("Wood_MM_Kuwahara", mm_kuwahara_source="Wood", mm_kuwahara_settings="{}")
preview = make("Wood_MM_Preview", mm_kuwahara_source="Wood", mm_kuwahara_settings="{}", mm_kuwahara_preview=True)
orphan = make("Gone_MM_Kuwahara", mm_kuwahara_source="Deleted", mm_kuwahara_settings="{}")

# source: the open image, or the original of a result / preview
check(resolve_source(context(wood)), wood, "plain image is its own source")
check(resolve_source(context(result)), wood, "a result goes back to its original")
check(resolve_source(context(preview)), wood, "a preview goes back to its original")
check(resolve_source(context(other, pinned=wood)), other, "the open image beats a leftover pinned image")
check(resolve_source(context(orphan)), orphan, "a deleted original falls back to the open image")
check(resolve_source(context(False, pinned=wood)), wood, "no editor (scripts, tests): the image property")
check(resolve_source(context(False)), None, "no editor and no image property: nothing")
check(resolve_source(context(None)), None, "empty editor: nothing")

# export: a result in the editor, else the last result, never while a preview is shown
check(result_image(context(result)), result, "a result open in the editor is exported")
check(result_image(context(wood, last_result=result)), result, "another image open: the last result")
check(result_image(context(wood)), None, "nothing to export yet")
check(result_image(context(preview, last_result=result)), None, "a preview open: export is off")
check(result_image(context(None, last_result=result)), result, "empty editor: the last result")

print(f"{checks} checks")
