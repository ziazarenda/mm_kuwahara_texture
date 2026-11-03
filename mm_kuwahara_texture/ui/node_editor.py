# Material Maker anisotropic Kuwahara: experimental Blender texture port.
# SPDX-License-Identifier: MIT
# See LICENSES.txt inside the delivered ZIP for full notices and sources.
# The sidebar tab in the Shader Editor (put a result into Image Texture nodes).

import bpy

from ..images import selected_image_nodes
from .common import BUTTON_SCALE_Y


class MMK_PT_NodePanel(bpy.types.Panel):
    bl_label = 'Material Maker Kuwahara'
    bl_idname = 'NODE_PT_mm_kuwahara'
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = 'MM Kuwahara'

    @classmethod
    def poll(cls, context):
        return getattr(context.space_data, 'tree_type', '') == 'ShaderNodeTree'

    def draw(self, context):
        box = self.layout.box()
        col = box.column()
        col.prop(context.scene.mm_kuwahara, 'node_image', text='Result')
        nodes = selected_image_nodes(context)
        col.label(text=f'Selected Image Texture nodes: {len(nodes)}')
        col = box.column()
        col.scale_y = BUTTON_SCALE_Y
        col.operator('image.mm_kuwahara_apply_to_nodes', text='Use Result in Selected Nodes')
