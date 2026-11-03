"""Stand-ins for bpy, gpu and friends so the add-on module can be imported
without Blender. Not a test (the leading underscore keeps _run_all.py away).

    module, shaders = load_addon(path_to___init__.py)

The functions live in the package's submodules: module.engine, module.operators, ...

`shaders` records every GPUShaderCreateInfo the add-on built, so tests can
read the exact GLSL that would be compiled.
"""
import importlib.util
import sys
import types
from pathlib import Path


class _Info:
    """Records what make_shader() gives to gpu.types.GPUShaderCreateInfo."""

    created = []

    def __init__(self):
        self.vertex = None
        self.fragment = None
        _Info.created.append(self)

    def vertex_in(self, *args): pass
    def fragment_out(self, *args): pass
    def sampler(self, *args): pass
    def push_constant(self, *args): pass
    def vertex_source(self, text): self.vertex = text
    def fragment_source(self, text): self.fragment = text


def _property(*args, **kwargs):
    return None


def _install():
    bpy = types.ModuleType("bpy")
    bpy.types = types.SimpleNamespace(
        PropertyGroup=type("PropertyGroup", (), {}),
        Operator=type("Operator", (), {}),
        Panel=type("Panel", (), {}),
        Image=type("Image", (), {}),
        Scene=type("Scene", (), {}),
    )
    bpy.props = types.ModuleType("bpy.props")

    for name in ("PointerProperty", "IntProperty", "FloatProperty", "EnumProperty",
                 "BoolProperty", "StringProperty"):
        setattr(bpy.props, name, _property)

    gpu = types.ModuleType("gpu")
    gpu.types = types.SimpleNamespace(GPUShaderCreateInfo=_Info)
    gpu.shader = types.SimpleNamespace(create_from_info=lambda info: info)

    extras = types.ModuleType("bpy_extras")
    io_utils = types.ModuleType("bpy_extras.io_utils")
    io_utils.ExportHelper = type("ExportHelper", (), {})
    gpu_extras = types.ModuleType("gpu_extras")
    batch = types.ModuleType("gpu_extras.batch")
    batch.batch_for_shader = lambda *args, **kwargs: None

    sys.modules.update({
        "bpy": bpy, "bpy.props": bpy.props, "gpu": gpu,
        "bpy_extras": extras, "bpy_extras.io_utils": io_utils,
        "gpu_extras": gpu_extras, "gpu_extras.batch": batch,
    })


def load_addon(path, name="addon_under_test"):
    _install()
    _Info.created.clear()
    path = Path(path)
    # The add-on is a package (engine.py, operators.py, ui/ ...), so load it as one:
    # its relative imports need a package name and a search location.
    spec = importlib.util.spec_from_file_location(name, path, submodule_search_locations=[str(path.parent)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module, _Info.created
