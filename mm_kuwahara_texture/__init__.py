# Material Maker anisotropic Kuwahara: experimental Blender texture port.
# SPDX-License-Identifier: MIT
# Filter shader: Copyright (c) 2022 Garrett Gunnell.
# Material Maker adaptations: Copyright (c) 2018-present Rodolphe Suescun and contributors.
# See LICENSES.txt inside the delivered ZIP for full notices and sources.
# Version 1.0.0: GLSL moved to shaders/*.glsl (byte-identical), crash fix when run without an area,
# now a regular add-on (bl_info) instead of an extension.
# Version 0.5.0: correct byte-image color handling; installable extension.
# Standalone processing; this does not install a live material/shader node.

bl_info = {
    "name": "Material Maker Kuwahara Texture",
    "author": "Texture port contributors",
    "version": (1, 0, 0),
    "blender": (5, 2, 0),
    "location": "Image Editor > Sidebar > MM Kuwahara",
    "description": "Apply Material Maker's anisotropic Kuwahara to image textures",
    "category": "Image",
}

import bpy
import gpu
import numpy as np
import json
import os
import struct
import tempfile
import zlib
from pathlib import Path
from bpy_extras.io_utils import ExportHelper
from gpu_extras.batch import batch_for_shader
from bpy.props import PointerProperty, IntProperty, FloatProperty, EnumProperty, BoolProperty, StringProperty


ADDON_VERSION = '.'.join(str(part) for part in bl_info['version'])

# The filter shaders are stored in shaders/*.glsl byte for byte as they were when
# they lived in this file. tests/plain/shader_text.py pins their hashes, so a
# change to the shader text (the thing that matches Material Maker) cannot slip
# in unnoticed. If you edit a shader on purpose, update the hashes in that test.
SHADER_DIR = Path(__file__).parent / 'shaders'


def load_glsl(name):
    """Read a shader file as stored (only Windows line endings are normalized)."""
    return (SHADER_DIR / name).read_bytes().decode('utf-8').replace('\r\n', '\n')


# Preserved Material Maker filter functions, with input/parameter substitutions.
TENSOR_GLSL = load_glsl('tensor.glsl')
KUWAHARA_GLSL = load_glsl('kuwahara.glsl')

# Texture access is explicit so source interpolation and tensor nearest sampling
# do not depend on Blender's globally shared texture sampler state.
SAMPLING_GLSL = load_glsl('sampling.glsl')
BLUR_GLSL = load_glsl('blur.glsl')


def make_shader(stage):
    info = gpu.types.GPUShaderCreateInfo()
    info.vertex_in(0, 'VEC2', 'position')
    info.fragment_out(0, 'VEC4', 'out_color')
    info.sampler(0, 'FLOAT_2D', 'color_tex')
    info.sampler(1, 'FLOAT_2D', 'tensor_tex')
    info.push_constant('FLOAT', 'resolution')
    info.push_constant('FLOAT', 'frame_resolution')
    info.push_constant('FLOAT', 'uniformity')
    info.push_constant('VEC2', 'blur_axis')
    info.push_constant('INT', 'filter_size')
    info.push_constant('FLOAT', 'sharpness_value')
    info.push_constant('FLOAT', 'eccentricity_value')
    info.vertex_source('void main(){gl_Position=vec4(position,0.0,1.0);}')
    if stage == 'tensor':
        functions = TENSOR_GLSL
        expression = 'calc_tensor(uv)'
    elif stage == 'blur':
        functions = BLUR_GLSL
        expression = 'blur_tensor(uv)'
    elif stage == 'export':
        functions = ''
        expression = 'read_linear(color_tex, uv)'
    else:
        functions = KUWAHARA_GLSL
        # Material Maker halves the public Eccentricity value here.
        expression = 'vec4(filter_kuwahara(uv,filter_size,sharpness_value,eccentricity_value*0.5),1.0)'
    info.fragment_source(SAMPLING_GLSL + functions + '\nvoid main(){\n'
        'vec2 uv=gl_FragCoord.xy/vec2(frame_resolution);\n'
        'out_color=' + expression + ';\n}\n')
    return gpu.shader.create_from_info(info)



def material_maker_byte_input(pixels):
    """Mirror Godot 4.4--4.6 byte-image -> RGBAH mantissa truncation.

    Input is a normalized byte buffer (guarded by source.is_float == False).
    Its nonzero components are >= 1/255, so no half subnormal handling is needed.
    Return float32 values exactly representable by half; uploading them to
    RGBA16F cannot introduce a second, different rounding step.
    """
    a = np.asarray(pixels, dtype=np.float32)
    return (a.view(np.uint32) & np.uint32(0xFFFFE000)).view(np.float32)



def write_lossless_rgba_png(filepath, rgba, compression=100, srgb=True):
    """Save exact top-first RGBA8 bytes using adaptive PNG row filters.

    Standard-library PNG/zlib writer; no image libraries, rendering, color
    conversion, resize, or quantization. Compression only controls zlib effort.
    The file is replaced atomically after the complete PNG is written.
    """
    rgba = np.asarray(rgba)
    if rgba.dtype != np.uint8 or rgba.ndim != 3 or rgba.shape[2] != 4:
        raise ValueError('PNG exporter requires an H x W x 4 uint8 array')
    height, width, _ = rgba.shape
    if width < 1 or height < 1:
        raise ValueError('Cannot export an empty image')
    if not 0 <= int(compression) <= 100:
        raise ValueError('Compression must be between 0 and 100')
    filepath = Path(filepath)
    level = int(int(compression) * 9 / 100 + 0.5)
    compressor = zlib.compressobj(level)
    temporary_path = None

    def chunk(handle, kind, data):
        handle.write(struct.pack('>I', len(data)))
        handle.write(kind)
        handle.write(data)
        handle.write(struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF))

    try:
        with tempfile.NamedTemporaryFile(mode='wb', dir=filepath.parent,
                prefix='.mm-kuwahara-', suffix='.tmp', delete=False) as handle:
            temporary_path = handle.name
            handle.write(b'\x89PNG\r\n\x1a\n')
            chunk(handle, b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            if srgb:
                chunk(handle, b'sRGB', b'\x00')
            previous = np.zeros(width * 4, dtype=np.int16)
            for row in rgba:
                current = row.reshape(-1).astype(np.int16)
                left = np.zeros_like(current)
                left[4:] = current[:-4]
                upper_left = np.zeros_like(previous)
                upper_left[4:] = previous[:-4]
                # PNG's Paeth predictor, preserving its specified tie order.
                predictor = left + previous - upper_left
                pa = np.abs(predictor - left)
                pb = np.abs(predictor - previous)
                pc = np.abs(predictor - upper_left)
                paeth = np.where((pa <= pb) & (pa <= pc), left,
                                 np.where(pb <= pc, previous, upper_left))
                candidates = [current, current-left, current-previous,
                              current-((left+previous)//2), current-paeth]
                candidates = [(candidate & 255).astype(np.uint8) for candidate in candidates]
                # libpng-style heuristic: absolute signed residual magnitude.
                scores = [int(np.minimum(c.astype(np.int16),
                            256-c.astype(np.int16)).sum()) for c in candidates]
                filter_type = int(np.argmin(scores))
                data = compressor.compress(bytes([filter_type]) + candidates[filter_type].tobytes())
                if data:
                    chunk(handle, b'IDAT', data)
                previous = current
            data = compressor.flush()
            if data:
                chunk(handle, b'IDAT', data)
            chunk(handle, b'IEND', b'')
        os.replace(temporary_path, filepath)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


CAPTURED_BUFFERS = {}


class MMK_Settings(bpy.types.PropertyGroup):
    image: PointerProperty(name='Source image', type=bpy.types.Image)
    output_image: PointerProperty(name='Filtered image', type=bpy.types.Image)
    png_compression: IntProperty(name='PNG compression', default=80, min=0, max=100,
        description='Lossless compression: higher values usually save smaller files but take longer')
    capture_buffers: BoolProperty(name='Capture diagnostic buffers', default=True,
        description='Keep raw float arrays before PNG conversion; export them as one NPZ file')
    resolution: EnumProperty(name='Processing resolution', items=[
        (str(2**n), str(2**n)+' x '+str(2**n), '') for n in range(7,13)], default='512')
    export_resolution: EnumProperty(name='Output resolution', items=[
        ('SAME', 'Same as processing', 'No resampling pass'),
        *[(str(2**n), str(2**n)+' x '+str(2**n), '') for n in range(7,14)]], default='2048',
        description='Resample the completed filter result; does not change brushstroke size')
    size: IntProperty(name='Size', default=6, min=1, max=32)
    uniformity: FloatProperty(name='Uniformity', default=4.0, min=0.0, max=50.0)
    sharpness: FloatProperty(name='Sharpness', default=1.0, min=0.0, max=1.0)
    eccentricity: FloatProperty(name='Eccentricity', default=1.0, min=0.0, max=2.0)



class MMK_OT_Filter(bpy.types.Operator):
    bl_idname = 'image.mm_kuwahara_filter'
    bl_label = 'Create filtered texture'
    bl_description = 'GPU filter into a new image; source is preserved'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.mm_kuwahara
        source = settings.image or getattr(context.space_data, 'image', None)
        if source is None or not source.has_data:
            self.report({'ERROR'}, 'Select a loaded source image first')
            return {'CANCELLED'}
        if source.source in {'TILED','MOVIE','SEQUENCE','VIEWER'}:
            self.report({'ERROR'}, 'First version supports single still images only')
            return {'CANCELLED'}
        width, height = source.size[:]
        if width != height:
            self.report({'ERROR'}, 'Use a square image for matching Material Maker')
            return {'CANCELLED'}
        # rna_Image_pixels_get returns normalized bytes for byte images;
        # it does NOT apply the sRGB-to-linear conversion. Float buffers follow
        # different rules; reject them until explicitly supported and tested.
        if source.is_float:
            self.report({'ERROR'}, 'Use an 8-bit JPEG/PNG source; float images are not supported yet')
            return {'CANCELLED'}
        if source.channels != 4:
            self.report({'ERROR'}, 'This version requires a four-channel Blender image buffer')
            return {'CANCELLED'}
        if source.colorspace_settings.name not in {'sRGB','Non-Color','Raw'}:
            self.report({'ERROR'}, 'Use an sRGB color image or a Non-Color data image')
            return {'CANCELLED'}
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
            pixels = np.empty(width*height*4, dtype=np.float32)
            source.pixels.foreach_get(pixels)
            pixels = pixels.reshape(height,width,4)
            pixels = material_maker_byte_input(pixels)
            # Run the original functions in Material Maker's logical top-first
            # coordinates. This aligns vertical sampling/accumulation order,
            # not just the final displayed orientation.
            pixels = np.flipud(pixels).copy()
            capture = {'source': pixels.copy()} if settings.capture_buffers else {}
            # Preserve encoded RGB, matching Material Maker's input half precision.
            # No extra transfer function on input or output.
            if source.alpha_mode == 'PREMUL':
                raise ValueError('Use Straight alpha for this first version')
            color = gpu.types.GPUTexture((width,height), format='RGBA16F',
                data=gpu.types.Buffer('FLOAT', pixels.size, pixels.reshape(-1)))
            shaders = {key:make_shader(key) for key in ('tensor','blur','filter')}
            if output_res != res:
                shaders['export'] = make_shader('export')
            tensor = color  # Dummy binding on first pass; not read by tensor shader.
            passes = [('tensor',(0.0,0.0)),('blur',(1.0,0.0)),
                      ('blur',(0.0,1.0)),('filter',(0.0,0.0))]
            if output_res != res:
                passes.append(('export',(0.0,0.0)))
            gpu.state.blend_set('NONE')
            gpu.state.depth_test_set('NONE')
            for index,(stage,axis) in enumerate(passes):
                frame_res = output_res if stage == 'export' else res
                # Export samples the completed half-float texture BEFORE byte
                # quantization, with Material Maker's linear repeat sampling.
                pass_color = tensor if stage == 'export' else color
                target = gpu.types.GPUOffScreen(frame_res,frame_res,format='RGBA16F')
                targets.append(target)
                shader = shaders[stage]
                batch = batch_for_shader(shader,'TRI_STRIP',
                    {'position':((-1,-1),(1,-1),(-1,1),(1,1))})
                with target.bind():
                    gpu.state.viewport_set(0,0,frame_res,frame_res)
                    shader.bind()
                    # Some drivers optimize unused uniforms out of each stage.
                    def uniform(method, name, value):
                        try:
                            getattr(shader,method)(name,value)
                        except ValueError:
                            pass
                    uniform('uniform_sampler','color_tex',pass_color)
                    uniform('uniform_sampler','tensor_tex',tensor)
                    uniform('uniform_float','resolution',float(res))
                    uniform('uniform_float','frame_resolution',float(frame_res))
                    uniform('uniform_float','uniformity',settings.uniformity)
                    uniform('uniform_float','blur_axis',axis)
                    uniform('uniform_int','filter_size',settings.size)
                    uniform('uniform_float','sharpness_value',settings.sharpness)
                    uniform('uniform_float','eccentricity_value',settings.eccentricity)
                    batch.draw(shader)
                tensor = target.texture_color
                if settings.capture_buffers and index < 4:
                    key = ('tensor','blur_x','blur_y','filtered')[index]
                    capture[key] = np.array(tensor.read(),dtype=np.float32).reshape(res,res,4).copy()
                wm.progress_update(index+1)
            values = np.array(tensor.read(), dtype=np.float32).reshape(output_res,output_res,4)
            if not np.isfinite(values).all():
                raise ValueError('Non-finite shader output; no image was created')
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
            output['mm_kuwahara_settings'] = str({'resolution':res,'output_resolution':output_res,'size':settings.size,
                'uniformity':settings.uniformity,'sharpness':settings.sharpness,
                'eccentricity':settings.eccentricity,'version':ADDON_VERSION,'byte_values_preserved':True})
            output.pack()
            settings.output_image = output
            if settings.capture_buffers:
                metadata = {
                    'addon_version':ADDON_VERSION, 'blender_version':bpy.app.version_string,
                    'source_name':source.name, 'source_size':[width,height],
                    'source_colorspace':source.colorspace_settings.name,
                    'processing_resolution':res, 'output_resolution':output_res,
                    'size':settings.size, 'uniformity':settings.uniformity,
                    'sharpness':settings.sharpness, 'eccentricity':settings.eccentricity,
                    'source_gpu_format':'RGBA16F', 'source_conversion':'Godot byte-to-half truncation', 'sample_conversion':'measured half nearest ties upward', 'processing_coordinates':'Material Maker top-first', 'intermediate_gpu_format':'RGBA16F',
                    'orientation':'row 0 is TOP of image; matches Material Maker EXRs directly; no tensor sign correction',
                    'array_meaning':'raw shader values; no transfer function or PNG quantization',
                }
                capture['metadata_json'] = np.array(json.dumps(metadata))
                CAPTURED_BUFFERS.clear()
                CAPTURED_BUFFERS.update(capture)
            # No area when run from a script or a timer; only show the result if there is an editor.
            if (context.area is not None and context.area.type == 'IMAGE_EDITOR'
                    and context.space_data is not None):
                context.space_data.image = output
            self.report({'INFO'}, 'Created '+output.name+'; use Export PNG to save')
            return {'FINISHED'}
        except Exception as exc:
            import traceback
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


class MMK_OT_ExportPNG(bpy.types.Operator, ExportHelper):
    bl_idname = 'image.mm_kuwahara_export_png'
    bl_label = 'Export PNG'
    bl_description = 'Save the filtered image with lossless compression and unchanged pixel values'
    filename_ext = '.png'
    filter_glob: StringProperty(default='*.png', options={'HIDDEN'})
    compression: IntProperty(name='Compression', default=80, min=0, max=100,
        description='Lossless compression; higher values generally make smaller files but save slower')

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, 'mm_kuwahara', None)
        image = settings.output_image if settings else None
        return bool(image and image.has_data and not image.is_float)

    def invoke(self, context, event):
        settings = context.scene.mm_kuwahara
        self.compression = settings.png_compression
        if not self.filepath:
            self.filepath = bpy.path.ensure_ext(
                bpy.path.clean_name(settings.output_image.name), '.png')
        return ExportHelper.invoke(self, context, event)

    def draw(self, context):
        self.layout.prop(self, 'compression')
        self.layout.label(text='Lossless: pixel values stay unchanged.')

    def execute(self, context):
        settings = context.scene.mm_kuwahara
        image = settings.output_image
        if image is None or not image.has_data or image.is_float or image.channels != 4:
            self.report({'ERROR'}, 'Choose a generated 8-bit RGBA filtered image')
            return {'CANCELLED'}
        try:
            width, height = image.size[:]
            values = np.empty(width*height*4, dtype=np.float32)
            image.pixels.foreach_get(values)
            # Byte images return normalized encoded bytes, not scene-linear RGB.
            # Recover those bytes exactly and change only row storage convention.
            rgba = np.rint(np.clip(values.reshape(height,width,4),0,1)*255).astype(np.uint8)
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


class MMK_PT_Panel(bpy.types.Panel):
    bl_label = 'Material Maker Kuwahara 1.0'
    bl_idname = 'IMAGE_PT_mm_kuwahara'
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = 'MM Kuwahara'

    def draw(self, context):
        layout = self.layout
        s = context.scene.mm_kuwahara
        layout.prop(s,'image')
        layout.prop(s,'resolution')
        layout.prop(s,'export_resolution')
        for key in ('size','uniformity','sharpness','eccentricity'):
            layout.prop(s,key)
        layout.prop(s,'capture_buffers')
        layout.operator('image.mm_kuwahara_filter')
        layout.prop(s,'output_image')
        layout.prop(s,'png_compression')
        layout.operator('image.mm_kuwahara_export_png')
        layout.operator('image.mm_kuwahara_save_diagnostics')
        layout.label(text='Creates a new image; original stays intact.')
        layout.label(text='Square 8-bit JPEG/PNG textures.')


CLASSES = (MMK_Settings,MMK_OT_Filter,MMK_OT_ExportPNG,MMK_OT_SaveDiagnostics,MMK_PT_Panel)


def register():
    # Allow rerunning the script from the Text Editor during testing.
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


if __name__ == '__main__':
    register()
