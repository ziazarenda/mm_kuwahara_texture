# Material Maker anisotropic Kuwahara: experimental Blender texture port.
# SPDX-License-Identifier: MIT
# Filter shader: Copyright (c) 2022 Garrett Gunnell.
# Material Maker adaptations: Copyright (c) 2018-present Rodolphe Suescun and contributors.
# See LICENSES.txt inside the delivered ZIP for full notices and sources.
# Standalone processing; this does not install a live material/shader node.

bl_info = {
    "name": "Material Maker Kuwahara Texture",
    "author": "ziazarenda",
    "version": (1, 1, 0),
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
import traceback
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


def is_filter_result(image):
    """True for a full result made by this add-on (not a preview)."""
    return bool(image.get('mm_kuwahara_settings')) and not image.get('mm_kuwahara_preview')


class MMK_Settings(bpy.types.PropertyGroup):
    image: PointerProperty(name='Source image', type=bpy.types.Image)
    output_image: PointerProperty(name='Filtered image', type=bpy.types.Image)
    node_image: PointerProperty(name='Result', type=bpy.types.Image, poll=lambda self, image: is_filter_result(image),
        description='Filtered image to put in the selected Image Texture nodes. Only results made by this add-on are listed')
    png_compression: IntProperty(name='PNG compression', default=80, min=0, max=100,
        description='Lossless compression: higher values usually save smaller files but take longer')
    capture_buffers: BoolProperty(name='Capture diagnostic buffers', default=False,
        description='Keep the raw float results of every pass in memory so they can be saved as one NPZ file. '
                    'Uses a lot of RAM at high resolutions')
    resolution: EnumProperty(name='Processing resolution', items=[
        (str(2**n), str(2**n)+' x '+str(2**n), '') for n in range(7,13)], default='512')
    export_resolution: EnumProperty(name='Output resolution', items=[
        ('SAME', 'Same as processing', 'No resampling pass'),
        *[(str(2**n), str(2**n)+' x '+str(2**n), '') for n in range(7,14)]], default='2048',
        description='Resample the finished result. Does not change the brushstroke size')
    preview_size: EnumProperty(name='Preview size', items=[
        (str(n), str(n)+' x '+str(n), '') for n in (128, 256, 512)], default='256',
        description='Size of the area that Preview filters. It is taken from the middle of the image '
                    'at the processing resolution, so the strokes look the same as in the full result')
    size: IntProperty(name='Size', default=6, min=1, max=32)
    uniformity: FloatProperty(name='Uniformity', default=4.0, min=0.0, max=50.0)
    sharpness: FloatProperty(name='Sharpness', default=1.0, min=0.0, max=1.0)
    eccentricity: FloatProperty(name='Eccentricity', default=1.0, min=0.0, max=2.0)



def source_problem(source):
    """Say why the source image cannot be filtered, or return None when it can."""
    if source is None or not source.has_data:
        return 'Open a loaded image in the Image Editor first'
    if getattr(source, 'get', lambda key: None)('mm_kuwahara_preview'):
        return 'This is a preview image. Choose the original image as the source'
    if source.source in {'TILED','MOVIE','SEQUENCE','VIEWER'}:
        return 'First version supports single still images only'
    width, height = source.size[:]
    if width != height:
        return 'Use a square image for matching Material Maker'
    # rna_Image_pixels_get returns normalized bytes for byte images;
    # it does NOT apply the sRGB-to-linear conversion. Float buffers follow
    # different rules; reject them until explicitly supported and tested.
    if source.is_float:
        return 'Use an 8-bit JPEG or PNG source. Float images are not supported yet'
    if source.channels != 4:
        return 'This version requires a four-channel Blender image buffer'
    if source.colorspace_settings.name not in {'sRGB','Non-Color','Raw'}:
        return 'Use an sRGB color image or a Non-Color data image'
    return None


def resolve_source(context):
    """The image to filter.

    It is the image open in the Image Editor. If that is a result or a preview made
    by this add-on, its original is used instead, so changing the settings and filtering
    again starts from the original, not from the result. Without an editor image (a
    script, or a test) the `image` property is used.
    """
    image = getattr(context.space_data, 'image', None)
    if image is None:
        settings = getattr(context.scene, 'mm_kuwahara', None)
        return settings.image if settings else None
    origin = image.get('mm_kuwahara_source')
    original = bpy.data.images.get(origin) if origin else None
    return original if original is not None else image


def result_image(context):
    """The image Export saves: the result open in the editor, else the last result made.

    A preview open in the editor gives None, so Export is off while one is shown.
    """
    image = getattr(context.space_data, 'image', None)
    if image is not None and image.get('mm_kuwahara_preview'):
        return None
    if image is not None and image.get('mm_kuwahara_settings'):
        return image
    settings = getattr(context.scene, 'mm_kuwahara', None)
    return settings.output_image if settings else None


def selected_image_nodes(context):
    """The selected Image Texture nodes of the shader tree open in the Node Editor."""
    tree = getattr(context.space_data, 'edit_tree', None)
    if tree is None:
        return []
    return [node for node in tree.nodes if node.select and node.bl_idname == 'ShaderNodeTexImage']


def image_rgba(image):
    """Read an image's pixels as a height x width x 4 float32 array (bottom row first)."""
    width, height = image.size[:]
    values = np.empty(width*height*4, dtype=np.float32)
    image.pixels.foreach_get(values)
    return values.reshape(height,width,4)


def set_uniform(shader, method, name, value):
    """Set a shader uniform. Some drivers optimize unused uniforms out of each stage."""
    try:
        getattr(shader,method)(name,value)
    except ValueError:
        pass


def prepare_pixels(source):
    """Source pixels as Material Maker sees them: half-truncated, top row first."""
    if source.alpha_mode == 'PREMUL':
        raise ValueError('Use Straight alpha for this first version')
    pixels = material_maker_byte_input(image_rgba(source))
    # Run the original functions in Material Maker's logical top-first
    # coordinates. This aligns vertical sampling/accumulation order,
    # not just the final displayed orientation.
    return np.flipud(pixels).copy()


def source_texture(pixels):
    """Upload prepared pixels as RGBA16F. The encoded RGB is kept: no transfer function on input or output."""
    height, width = pixels.shape[:2]
    return gpu.types.GPUTexture((width,height), format='RGBA16F',
        data=gpu.types.Buffer('FLOAT', pixels.size, pixels.reshape(-1)))


PASSES = (('tensor',(0.0,0.0)),('blur',(1.0,0.0)),('blur',(0.0,1.0)),('filter',(0.0,0.0)))


def run_pipeline(color, res, output_res, settings, targets, regions=None, read_rect=None, on_pass=None):
    """Run the GPU passes on `color`; return (last texture, pixels read from read_rect).

    Every offscreen buffer created is added to `targets`, so the caller can free them
    even when a pass fails. `regions` limits each of the four filter passes to a
    rectangle (x, y, w, h); None runs them over the whole frame. `read_rect` reads
    that rectangle of the last pass straight from the GPU instead of the whole frame.
    `on_pass(index, texture)` is called after every pass.
    """
    shaders = {key:make_shader(key) for key in ('tensor','blur','filter')}
    passes = list(PASSES)
    if output_res != res:
        shaders['export'] = make_shader('export')
        passes.append(('export',(0.0,0.0)))
    tensor = color  # Dummy binding on first pass; not read by tensor shader.
    read_values = None
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
        x, y, width, height = (regions[index] if regions and index < len(regions)
                               else (0, 0, frame_res, frame_res))
        with target.bind():
            # A smaller viewport draws only that rectangle. Fragment coordinates
            # stay absolute, so every pixel gets the same value as in a full run.
            gpu.state.viewport_set(x,y,width,height)
            shader.bind()
            set_uniform(shader,'uniform_sampler','color_tex',pass_color)
            set_uniform(shader,'uniform_sampler','tensor_tex',tensor)
            set_uniform(shader,'uniform_float','resolution',float(res))
            set_uniform(shader,'uniform_float','frame_resolution',float(frame_res))
            set_uniform(shader,'uniform_float','uniformity',settings.uniformity)
            set_uniform(shader,'uniform_float','blur_axis',axis)
            set_uniform(shader,'uniform_int','filter_size',settings.size)
            set_uniform(shader,'uniform_float','sharpness_value',settings.sharpness)
            set_uniform(shader,'uniform_float','eccentricity_value',settings.eccentricity)
            batch.draw(shader)
            if read_rect is not None and index == len(passes) - 1:
                rx, ry, rw, rh = read_rect
                buffer = gpu.state.active_framebuffer_get().read_color(rx, ry, rw, rh, 4, 0, 'FLOAT')
                read_values = np.array(buffer, dtype=np.float32).reshape(rh, rw, 4)
        tensor = target.texture_color
        if on_pass is not None:
            on_pass(index, tensor)
    return tensor, read_values


# The blur passes reach 50 pixels each way (blur.glsl); one more is kept as a safety margin.
BLUR_REACH = 51


def preview_plan(res, size):
    """Plan a preview of the middle of a res x res frame.

    Returns (crop, regions). `crop` is the (x, y, w, h) rectangle to show; `regions`
    holds the rectangle each pass (tensor, blur X, blur Y, filter) has to cover so that
    the crop is identical to the same pixels of a full run. The filter reads the blurred
    tensor only at its own pixel, so it needs no extra border. Blur Y reads blur X
    BLUR_REACH rows up and down, and blur X reads the tensor BLUR_REACH columns
    left and right.
    """
    size = max(1, min(int(size), int(res)))
    x = y = (res - size) // 2
    crop = (x, y, size, size)
    reach = BLUR_REACH
    if size + 2 * reach > res:
        # Too close to the image edges (the sampling wraps around): do the whole frame.
        return crop, [(0, 0, res, res)] * 4
    return crop, [
        (x - reach, y - reach, size + 2 * reach, size + 2 * reach),  # tensor
        (x, y - reach, size, size + 2 * reach),                      # blur X
        (x, y, size, size),                                          # blur Y
        (x, y, size, size),                                          # filter
    ]


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
            # No area when run from a script or a timer; only show the result if there is an editor.
            if (context.area is not None and context.area.type == 'IMAGE_EDITOR'
                    and context.space_data is not None):
                context.space_data.image = output
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
            if preview is None:
                preview = bpy.data.images.new(source.name + '_MM_Preview', width=size, height=size,
                                              alpha=True, float_buffer=False)
                created = True
            elif tuple(preview.size) != (size, size):
                preview.scale(size, size)
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
            if (context.area is not None and context.area.type == 'IMAGE_EDITOR'
                    and context.space_data is not None):
                context.space_data.image = preview
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


BUTTON_SCALE_Y = 1.5
INPUT_SCALE_Y = 1.3


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


CLASSES = (MMK_Settings,MMK_OT_Filter,MMK_OT_Preview,MMK_OT_ApplyToNodes,MMK_OT_ExportPNG,
           MMK_OT_SaveDiagnostics,MMK_PT_Panel,MMK_PT_NodePanel)


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
