# Material Maker anisotropic Kuwahara: experimental Blender texture port.
# SPDX-License-Identifier: MIT
# See LICENSES.txt inside the delivered ZIP for full notices and sources.
# The GPU pipeline: shaders, passes, preview planning and the PNG writer. No panels or operators here.

import os
import struct
import tempfile
import zlib
from pathlib import Path

import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader


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
