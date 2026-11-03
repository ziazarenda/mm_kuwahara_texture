# Material Maker anisotropic Kuwahara: experimental Blender texture port.
# SPDX-License-Identifier: MIT
# See LICENSES.txt inside the delivered ZIP for full notices and sources.

import json
import os
import traceback

import bpy
import gpu
import numpy as np
from bpy.props import IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

# bl_info is defined at the top of __init__.py before this module is imported.
from . import bl_info
from .engine import (prepare_pixels, source_texture, run_pipeline, preview_plan,
                     image_rgba, write_lossless_rgba_png)
from .images import source_problem, resolve_source, result_image, selected_image_nodes

ADDON_VERSION = '.'.join(str(part) for part in bl_info['version'])

CAPTURED_BUFFERS = {}


def show_in_editor(context, image, fit):
    """Show `image` in the Image Editor the operator was started from, and frame it.

    The editor keeps its zoom when the image changes, so a bigger image would otherwise
    appear zoomed in. `fit` fills the view (a finished result); without it the whole
    image is shown at 100% or smaller (a preview, so its pixels are not enlarged).
    There is no area when this runs from a script or a timer: then nothing is shown.
    """
    area = context.area
    if area is None or area.type != 'IMAGE_EDITOR' or context.space_data is None:
        return
    context.space_data.image = image
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    if region is None:
        return
    try:
        with context.temp_override(area=area, region=region, space_data=context.space_data):
            bpy.ops.image.view_all(fit_view=fit)
    except RuntimeError:
        pass  # framing is only a convenience; never fail the filter because of it


class MMK_OT_Filter(bpy.types.Operator):
    bl_idname = 'image.mm_kuwahara_filter'
    bl_label = 'Create filtered texture'
    bl_description = 'Run the filter on the GPU and create a new image.'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.mm_kuwahara
        source = resolve_source(context)
        problem = source_problem(source)
        if problem:
            self.report({'ERROR'}, problem)
            return {'CANCELLED'}
        width, height = source.size[:]
        CAPTURED_BUFFERS.clear()
        targets = []
        output = None
        wm = context.window_manager
        viewport = gpu.state.viewport_get()
        blend = gpu.state.blend_get()
        depth = gpu.state.depth_test_get()
        try:
            res = int(settings.resolution)
            output_res = res if settings.export_resolution == 'SAME' else int(settings.export_resolution)
            wm.progress_begin(0, 4 + int(output_res != res))
            pixels = prepare_pixels(source)
            capture = {'source': pixels.copy()} if settings.capture_buffers else {}
            color = source_texture(pixels)
            def on_pass(index, texture):
                if settings.capture_buffers and index < 4:
                    key = ('tensor','blur_x','blur_y','filtered')[index]
                    capture[key] = np.array(texture.read(),dtype=np.float32).reshape(res,res,4).copy()
                wm.progress_update(index+1)

            tensor, _ = run_pipeline(color, res, output_res, settings, targets, on_pass=on_pass)
            values = np.array(tensor.read(), dtype=np.float32).reshape(output_res,output_res,4)
            if not np.isfinite(values).all():
                raise ValueError('The shader produced invalid values, so no image was created')
            # GPU/capture buffers above are top-first; Blender image.pixels
            # expects bottom-first storage. Flip only the displayed output back.
            values = np.flipud(values).copy()
            output = bpy.data.images.new(source.name+'_MM_Kuwahara',
                width=output_res,height=output_res,alpha=True,float_buffer=False)
            # Store the encoded result in a byte image tagged sRGB. Blender
            # performs the usual decode itself when sampling it in materials.
            output.colorspace_settings.name = ('Non-Color'
                if source.colorspace_settings.name in {'Non-Color','Raw'} else 'sRGB')
            output.alpha_mode = 'STRAIGHT'
            # Match Godot 4.4--4.6 Image RGBA8 conversion used by the
            # supplied Material Maker reference: truncate after scaling by 255.
            # Prequantize explicitly so Blender's round-to-nearest setter cannot
            # add the systematic +0/+1 byte difference.
            values = np.floor(np.clip(values, 0.0, 1.0) * 255.0) / 255.0
            output.pixels.foreach_set(values.reshape(-1))
            output.update()
            output.use_fake_user = True
            filter_settings = {'size':settings.size,'uniformity':settings.uniformity,
                'sharpness':settings.sharpness,'eccentricity':settings.eccentricity}
            output['mm_kuwahara_settings'] = str({'resolution':res,'output_resolution':output_res,
                **filter_settings,'version':ADDON_VERSION,'byte_values_preserved':True})
            output['mm_kuwahara_source'] = source.name
            output.pack()
            settings.output_image = output
            settings.node_image = output
            if settings.capture_buffers:
                metadata = {
                    'addon_version':ADDON_VERSION, 'blender_version':bpy.app.version_string,
                    'source_name':source.name, 'source_size':[width,height],
                    'source_colorspace':source.colorspace_settings.name,
                    'processing_resolution':res, 'output_resolution':output_res,
                    **filter_settings,
                    'source_gpu_format':'RGBA16F', 'source_conversion':'Godot byte-to-half truncation', 'sample_conversion':'measured half nearest ties upward', 'processing_coordinates':'Material Maker top-first', 'intermediate_gpu_format':'RGBA16F',
                    'orientation':'row 0 is TOP of image; matches Material Maker EXRs directly; no tensor sign correction',
                    'array_meaning':'raw shader values; no transfer function or PNG quantization',
                }
                capture['metadata_json'] = np.array(json.dumps(metadata))
                CAPTURED_BUFFERS.clear()
                CAPTURED_BUFFERS.update(capture)
            show_in_editor(context, output, fit=True)
            self.report({'INFO'}, 'Created '+output.name+'. Use Export PNG to save it')
            return {'FINISHED'}
        except Exception as exc:
            traceback.print_exc()
            if output is not None:
                bpy.data.images.remove(output)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        finally:
            gpu.state.viewport_set(*viewport)
            gpu.state.blend_set(blend)
            gpu.state.depth_test_set(depth)
            for target in reversed(targets):
                target.free()
            wm.progress_end()


class MMK_OT_Preview(bpy.types.Operator):
    bl_idname = 'image.mm_kuwahara_preview'
    bl_label = 'Preview filter'
    bl_description = ('Filter the middle of the texture with the current settings to check them quickly. '
                      'The preview image is temporary and the original stays unchanged')
    bl_options = {'REGISTER'}

    def execute(self, context):
        settings = context.scene.mm_kuwahara
        source = resolve_source(context)
        problem = source_problem(source)
        if problem:
            self.report({'ERROR'}, problem)
            return {'CANCELLED'}
        targets = []
        preview = None
        created = False
        wm = context.window_manager
        viewport = gpu.state.viewport_get()
        blend = gpu.state.blend_get()
        depth = gpu.state.depth_test_get()
        try:
            res = int(settings.resolution)
            crop, regions = preview_plan(res, int(settings.preview_size))
            color = source_texture(prepare_pixels(source))
            _, values = run_pipeline(color, res, res, settings, targets, regions=regions, read_rect=crop)
            if not np.isfinite(values).all():
                raise ValueError('The shader produced invalid values, so no preview was created')
            size = crop[2]
            # Same storage conversions as the full result (bottom row first, truncated bytes).
            values = np.floor(np.clip(np.flipud(values), 0.0, 1.0) * 255.0) / 255.0
            # Reuse this source's earlier preview (found by its marker, never by name alone),
            # so nothing that points at it breaks and previews do not pile up.
            preview = next((image for image in bpy.data.images
                            if image.get('mm_kuwahara_preview') and image.get('mm_kuwahara_source') == source.name), None)
            if preview is not None and tuple(preview.size) != (size, size):
                # Resizing an existing image with scale() leaves its pixel array at the old size,
                # so writing the new pixels fails. A preview of another size is made again instead.
                bpy.data.images.remove(preview)
                preview = None
            if preview is None:
                preview = bpy.data.images.new(source.name + '_MM_Preview', width=size, height=size,
                                              alpha=True, float_buffer=False)
                created = True
            preview.colorspace_settings.name = ('Non-Color'
                if source.colorspace_settings.name in {'Non-Color','Raw'} else 'sRGB')
            preview.alpha_mode = 'STRAIGHT'
            preview.pixels.foreach_set(values.reshape(-1))
            preview.update()
            preview['mm_kuwahara_preview'] = True
            preview['mm_kuwahara_source'] = source.name
            preview['mm_kuwahara_settings'] = str({'resolution':res,'preview_size':size,
                'size':settings.size,'uniformity':settings.uniformity,'sharpness':settings.sharpness,
                'eccentricity':settings.eccentricity,'version':ADDON_VERSION})
            show_in_editor(context, preview, fit=False)
            self.report({'INFO'}, f'Preview of the middle {size} x {size} pixels at {res} x {res}')
            return {'FINISHED'}
        except Exception as exc:
            traceback.print_exc()
            if preview is not None and created:
                bpy.data.images.remove(preview)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        finally:
            gpu.state.viewport_set(*viewport)
            gpu.state.blend_set(blend)
            gpu.state.depth_test_set(depth)
            for target in reversed(targets):
                target.free()


class MMK_OT_ApplyToNodes(bpy.types.Operator):
    bl_idname = 'image.mm_kuwahara_apply_to_nodes'
    bl_label = 'Use result in selected nodes'
    bl_description = 'Replace the image of the selected Image Texture nodes with the chosen filtered result'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        image = context.scene.mm_kuwahara.node_image
        return bool(image and image.has_data and selected_image_nodes(context))

    def execute(self, context):
        image = context.scene.mm_kuwahara.node_image
        nodes = selected_image_nodes(context)
        if image is None or not nodes:
            self.report({'ERROR'}, 'Select an Image Texture node and filter an image first')
            return {'CANCELLED'}
        for node in nodes:
            node.image = image
        self.report({'INFO'}, f'Set {image.name} on {len(nodes)} node(s)')
        return {'FINISHED'}


class MMK_OT_ExportPNG(bpy.types.Operator, ExportHelper):
    bl_idname = 'image.mm_kuwahara_export_png'
    bl_label = 'Export PNG'
    bl_description = 'Save the filtered image with lossless compression and unchanged pixel values'
    filename_ext = '.png'
    filter_glob: StringProperty(default='*.png', options={'HIDDEN'})
    compression: IntProperty(name='Compression', default=80, min=0, max=100,
        description='Lossless compression. Higher values usually make smaller files but take longer to save')

    @classmethod
    def poll(cls, context):
        image = result_image(context)
        return bool(image and image.has_data and not image.is_float)

    def invoke(self, context, event):
        settings = context.scene.mm_kuwahara
        self.compression = settings.png_compression
        if not self.filepath:
            self.filepath = bpy.path.ensure_ext(
                bpy.path.clean_name(result_image(context).name), '.png')
        return ExportHelper.invoke(self, context, event)

    def draw(self, context):
        self.layout.prop(self, 'compression')
        self.layout.label(text='Lossless: pixel values stay unchanged.')

    def execute(self, context):
        settings = context.scene.mm_kuwahara
        image = result_image(context)
        if image is None or not image.has_data or image.is_float or image.channels != 4:
            self.report({'ERROR'}, 'Choose a generated 8-bit RGBA filtered image')
            return {'CANCELLED'}
        try:
            # Byte images return normalized encoded bytes, not scene-linear RGB.
            # Recover those bytes exactly and change only row storage convention.
            rgba = np.rint(np.clip(image_rgba(image),0,1)*255).astype(np.uint8)
            rgba = np.flipud(rgba)
            filepath = bpy.path.abspath(bpy.path.ensure_ext(self.filepath, '.png'))
            write_lossless_rgba_png(filepath, rgba, self.compression,
                                   srgb=image.colorspace_settings.name == 'sRGB')
            settings.png_compression = self.compression
            size_mib = os.path.getsize(filepath) / (1024*1024)
            self.report({'INFO'}, f'Saved lossless PNG ({size_mib:.2f} MiB)')
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class MMK_OT_SaveDiagnostics(bpy.types.Operator, ExportHelper):
    bl_idname = 'image.mm_kuwahara_save_diagnostics'
    bl_label = 'Save diagnostic buffers'
    bl_description = 'Save all captured float buffers into one lossless NPZ file'
    filename_ext = '.npz'
    filter_glob: StringProperty(default='*.npz', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return bool(CAPTURED_BUFFERS)

    def execute(self, context):
        try:
            np.savez_compressed(self.filepath, **CAPTURED_BUFFERS)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Saved source, tensor, X/Y blur and filtered floats')
        return {'FINISHED'}


classes = (MMK_OT_Filter, MMK_OT_Preview, MMK_OT_ApplyToNodes, MMK_OT_ExportPNG, MMK_OT_SaveDiagnostics)
