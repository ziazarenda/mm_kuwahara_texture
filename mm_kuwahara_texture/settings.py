# Material Maker anisotropic Kuwahara: experimental Blender texture port.
# SPDX-License-Identifier: MIT
# See LICENSES.txt inside the delivered ZIP for full notices and sources.

from bpy.props import PointerProperty, IntProperty, FloatProperty, EnumProperty, BoolProperty
import bpy

from .images import is_filter_result


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
