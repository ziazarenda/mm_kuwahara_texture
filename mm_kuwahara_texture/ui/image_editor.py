# Material Maker anisotropic Kuwahara: experimental Blender texture port.
# SPDX-License-Identifier: MIT
# See LICENSES.txt inside the delivered ZIP for full notices and sources.
# The sidebar tab in the Image Editor (filter, preview, export).

import bpy

from ..images import resolve_source
from .common import BUTTON_SCALE_Y, INPUT_SCALE_Y


class MMK_PT_Panel(bpy.types.Panel):
    bl_label = 'Material Maker Kuwahara'
    bl_idname = 'IMAGE_PT_mm_kuwahara'
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = 'MM Kuwahara'

    def draw(self, context):
        layout = self.layout
        s = context.scene.mm_kuwahara

        box = layout.box()
        col = box.column()
        col.scale_y = INPUT_SCALE_Y

        source = resolve_source(context)
        col.label(text='Source: ' + (source.name if source else 'open an image'), icon='IMAGE_DATA')
        col.prop(s, 'resolution', text='Process')
        col.prop(s, 'export_resolution', text='Output')

        for key in ('size','uniformity','sharpness','eccentricity'):
            col.prop(s,key)

        col.prop(s, 'capture_buffers', text='Capture Buffers')
        col.prop(s, 'preview_size', text='Preview')

        col = box.column()
        col.scale_y = BUTTON_SCALE_Y
        row = col.row(align=True)
        row.operator('image.mm_kuwahara_preview', text='Preview')
        row.operator('image.mm_kuwahara_filter', text='Filter Texture')

        box = layout.box()
        col = box.column()
        col.scale_y = INPUT_SCALE_Y
        col.prop(s, 'png_compression', text='Compression')

        col = box.column()
        col.scale_y = BUTTON_SCALE_Y
        col.operator('image.mm_kuwahara_export_png', text='Export Lossless PNG')

        if s.capture_buffers:
            col.operator('image.mm_kuwahara_save_diagnostics')

        note = layout.box()
        col = note.column(align=True)
        col.scale_y = 0.85
        col.label(text='Square 8-bit PNG / JPG only', icon='INFO')
        col.label(text='Non-destructive (creates new image)')
