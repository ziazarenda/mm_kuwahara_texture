# Material Maker anisotropic Kuwahara: experimental Blender texture port.
# SPDX-License-Identifier: MIT
# Filter shader: Copyright (c) 2022 Garrett Gunnell.
# Material Maker adaptations: Copyright (c) 2018-present Rodolphe Suescun and contributors.
# See LICENSES.txt inside the delivered ZIP for full notices and sources.
# Standalone processing; this does not install a live material/shader node.

bl_info = {
    "name": "Material Maker Kuwahara Texture",
    "author": "ziazarenda",
    "version": (1, 1, 1),
    "blender": (5, 2, 0),
    "location": "Image Editor > Sidebar > MM Kuwahara",
    "description": "Apply Material Maker's anisotropic Kuwahara to image textures",
    "category": "Image",
}

import bpy
from bpy.props import PointerProperty

from . import operators, ui
from .settings import MMK_Settings

CLASSES = (MMK_Settings, *operators.classes, *ui.classes)


def register():
    # Drop leftovers of an earlier registration, so reloading the add-on during development works.
    if hasattr(bpy.types.Scene,'mm_kuwahara'):
        del bpy.types.Scene.mm_kuwahara
    for cls in reversed(CLASSES):
        old = getattr(bpy.types,cls.__name__,None)
        if old is not None:
            bpy.utils.unregister_class(old)
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.mm_kuwahara = PointerProperty(type=MMK_Settings)


def unregister():
    if hasattr(bpy.types.Scene,'mm_kuwahara'):
        del bpy.types.Scene.mm_kuwahara
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
