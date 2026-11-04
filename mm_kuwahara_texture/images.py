# Material Maker anisotropic Kuwahara: experimental Blender texture port.
# SPDX-License-Identifier: MIT
# See LICENSES.txt inside the delivered ZIP for full notices and sources.
# Small helpers that look at Blender state: which image to filter, which result to use, which nodes are selected.

import bpy


def is_filter_result(image):
    """True for a full result made by this add-on (not a preview)."""
    return bool(image.get('mm_kuwahara_settings')) and not image.get('mm_kuwahara_preview')


def mark_generated(image, source):
    """Tag a generated image with its original (pointer survives renames; name is a fallback)."""
    image['mm_kuwahara_has_ref'] = True
    image['mm_kuwahara_source_ref'] = source
    image['mm_kuwahara_source'] = source.name


def original_of(image):
    """The original image a generated image came from, or None if it can't be found."""
    if image.get('mm_kuwahara_has_ref'):
        return image.get('mm_kuwahara_source_ref')   # None if the original was deleted
    name = image.get('mm_kuwahara_source')
    return bpy.data.images.get(name) if name else None


def is_preview_of(image, source):
    return bool(image.get('mm_kuwahara_preview')) and original_of(image) == source


def source_problem(source):
    """Say why the source image cannot be filtered, or return None when it can."""
    if source is None or not source.has_data:
        return 'Open a loaded image in the Image Editor first'
    if getattr(source, 'get', lambda key: None)('mm_kuwahara_preview'):
        return 'This is a preview image. Choose the original image as the source'
    if source.get('mm_kuwahara_settings'):
        return 'This is a filtered result and its original image is missing or was renamed. Choose the original image'
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
        return 'Unsupported image channel layout'
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
    if image.get('mm_kuwahara_source') or image.get('mm_kuwahara_source_ref'):
        original = original_of(image)
        return original if original is not None else image   # source_problem() rejects it below
    return image


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
