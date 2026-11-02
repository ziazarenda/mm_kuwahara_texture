"""Check which nodes the 'use result' button would change (no Blender needed)."""
import types
from pathlib import Path

source = (Path(__file__).resolve().parents[2] / "mm_kuwahara_texture" / "__init__.py").read_text(encoding="utf-8")
start = source.index("def selected_image_nodes")
end = source.index("def image_rgba")
namespace = {}
exec(source[start:end], namespace)
pick = namespace["selected_image_nodes"]


def node(kind, selected):
    return types.SimpleNamespace(bl_idname=kind, select=selected)


def ctx(tree):
    return types.SimpleNamespace(space_data=types.SimpleNamespace(edit_tree=tree))


tex_a, tex_b = node("ShaderNodeTexImage", True), node("ShaderNodeTexImage", True)
tex_off = node("ShaderNodeTexImage", False)
bsdf = node("ShaderNodeBsdfPrincipled", True)
tree = types.SimpleNamespace(nodes=[tex_a, tex_off, bsdf, tex_b])

assert pick(ctx(tree)) == [tex_a, tex_b]
assert pick(ctx(types.SimpleNamespace(nodes=[bsdf, tex_off]))) == []
assert pick(ctx(None)) == []
assert pick(types.SimpleNamespace(space_data=types.SimpleNamespace())) == []
assert pick(types.SimpleNamespace(space_data=None)) == []

start = source.index("def is_filter_result")
end = source.index("class MMK_Settings")
ns = {}
exec(source[start:end], ns)
keep = ns["is_filter_result"]


class Img(dict):
    pass


assert keep(Img(mm_kuwahara_settings="x"))
assert not keep(Img(mm_kuwahara_settings="x", mm_kuwahara_preview=True))
assert not keep(Img())
print("node_pick: 8 checks passed")
