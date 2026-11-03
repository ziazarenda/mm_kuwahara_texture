# Material Maker anisotropic Kuwahara: experimental Blender texture port.
# SPDX-License-Identifier: MIT
# See LICENSES.txt inside the delivered ZIP for full notices and sources.

from .image_editor import MMK_PT_Panel
from .node_editor import MMK_PT_NodePanel

classes = (MMK_PT_Panel, MMK_PT_NodePanel)
