# EL View - Eye Level guide overlay for Blender
# Author: stechdrive
# Repository: https://github.com/stechdrive/el-view
#
# License: MIT License
#
# Copyright (c) 2025 stechdrive
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

bl_info = {
    "name": "EL View",
    "author": "stechdrive",
    "version": (2, 0, 0),
    "blender": (4, 0, 0),
    "location": "3D View > N Panel > View > EL View",
    "description": "Display eye level (horizon) line on camera view and render output",
    "category": "3D View",
    "doc_url": "https://github.com/stechdrive/el-view",
}

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from bpy.types import PropertyGroup, Panel
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty, PointerProperty
from mathutils import Vector, Matrix
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Property Group
# ---------------------------------------------------------------------------

class ELViewSettings(PropertyGroup):
    """Settings for the eye level line overlay."""

    enable: BoolProperty(
        name="Enable",
        default=False,
        description="Show eye level line in camera view",
    )
    color: FloatVectorProperty(
        name="Color",
        subtype='COLOR',
        size=4,
        default=(1.0, 0.0, 0.0, 0.8),
        min=0.0,
        max=1.0,
        description="Line color and opacity",
    )
    line_width: FloatProperty(
        name="Line Width",
        default=2.0,
        min=1.0,
        max=10.0,
        description="Line thickness in pixels",
    )
    render_overlay: BoolProperty(
        name="Render Overlay",
        default=True,
        description="Composite eye level line onto render output",
    )


# ---------------------------------------------------------------------------
# Core calculation
# ---------------------------------------------------------------------------

def _get_eye_level_sample(cam_obj: bpy.types.Object) -> Optional[Vector]:
    """Return a world-space point on the eye-level plane directly ahead of *cam_obj*.

    Returns ``None`` when the camera points straight up or down
    (eye level is undefined in that orientation).
    """
    cam_pos: Vector = cam_obj.matrix_world.translation

    # Camera forward vector (local -Z in world space)
    forward: Vector = (cam_obj.matrix_world.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()

    # Project forward onto the XY plane to get horizontal forward
    forward_xy = Vector((forward.x, forward.y, 0.0))
    if forward_xy.length < 1e-6:
        return None
    forward_xy.normalize()

    sample: Vector = cam_pos + forward_xy * 100.0
    sample.z = cam_pos.z  # eye level = camera world Z
    return sample


def _calc_eye_level_ndc_y_for_render(scene: bpy.types.Scene) -> Optional[float]:
    """Calculate the NDC Y of the eye level line for render output.

    Uses ``view_frame()`` and simple perspective math to compute the
    NDC Y without requiring a depsgraph.  This works reliably in
    render handler contexts where ``bpy.context`` is restricted.
    """
    cam_obj = scene.camera
    if cam_obj is None:
        return None

    sample = _get_eye_level_sample(cam_obj)
    if sample is None:
        return None

    cam_data = cam_obj.data

    # view_frame returns the 4 frustum corners in camera local space.
    # No depsgraph needed — only camera data and scene render settings.
    frame = cam_data.view_frame(scene=scene)

    # Frustum depth (all corners share the same local Z)
    depth: float = -frame[0].z
    if depth <= 0.0:
        return None

    # Vertical extent of the view plane
    ys = [co.y for co in frame]
    bottom_y: float = min(ys)
    top_y: float = max(ys)
    frame_height: float = top_y - bottom_y
    if frame_height <= 0.0:
        return None

    # Transform sample point from world to camera local space
    sample_local: Vector = cam_obj.matrix_world.inverted() @ sample

    # Project onto the view plane
    if cam_data.type == 'PERSP':
        if sample_local.z >= 0.0:
            return None  # behind camera
        proj_y: float = sample_local.y * depth / (-sample_local.z)
    elif cam_data.type == 'ORTHO':
        proj_y = sample_local.y
    else:
        return None  # panoramic etc. — not supported

    # Normalise to NDC Y  [-1, 1]
    ndc_y: float = (2.0 * (proj_y - bottom_y) / frame_height) - 1.0

    if ndc_y < -1.0 or ndc_y > 1.0:
        return None

    return ndc_y


# ---------------------------------------------------------------------------
# Viewport overlay drawing
# ---------------------------------------------------------------------------

_draw_handle = None


def _draw_callback() -> None:
    """GPU draw callback registered to SpaceView3D POST_PIXEL."""
    from bpy_extras.view3d_utils import location_3d_to_region_2d

    context = bpy.context
    scene = context.scene

    settings = getattr(scene, "elview_settings", None)
    if settings is None or not settings.enable:
        return

    region = context.region
    rv3d = context.region_data
    if rv3d is None or rv3d.view_perspective != 'CAMERA':
        return

    cam_obj = scene.camera
    if cam_obj is None:
        return

    sample = _get_eye_level_sample(cam_obj)
    if sample is None:
        return

    # Project the sample point directly to viewport pixel coordinates.
    # location_3d_to_region_2d handles all viewport/camera/NDC mapping
    # correctly, including letterboxing and camera zoom/offset.
    px = location_3d_to_region_2d(region, rv3d, sample)
    if px is None:
        return

    pixel_y: float = px.y

    # Clip to viewport region
    if pixel_y < 0 or pixel_y > region.height:
        return

    color = tuple(settings.color)
    width: float = settings.line_width

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    coords = [(0, pixel_y), (region.width, pixel_y)]
    batch = batch_for_shader(shader, 'LINES', {"pos": coords})

    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(width)

    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)

    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')


# ---------------------------------------------------------------------------
# Render overlay — compositor node injection
# ---------------------------------------------------------------------------
#
# Render Result pixels are not accessible from Python (Blender #53768).
# Instead, we inject a temporary Image + Alpha Over node into the
# compositor tree just before rendering.  The nodes are removed once
# the render finishes or is cancelled.
# ---------------------------------------------------------------------------

_OVERLAY_IMG_NAME: str = "__elview_overlay__"
_OVERLAY_NODE_IMG: str = "__elview_img_node__"
_OVERLAY_NODE_MIX: str = "__elview_alpha_node__"

# Stores state needed to restore the compositor after render.
_comp_cleanup: Optional[dict] = None


def _create_overlay_image(scene: bpy.types.Scene, ndc_y: float) -> Optional[bpy.types.Image]:
    """Create (or update) a transparent image with the eye-level line."""
    settings = scene.elview_settings
    render = scene.render
    scale: float = render.resolution_percentage / 100.0
    img_w: int = int(render.resolution_x * scale)
    img_h: int = int(render.resolution_y * scale)
    if img_w <= 0 or img_h <= 0:
        return None

    pixel_y: int = int((ndc_y + 1.0) * 0.5 * img_h)
    line_w: int = max(1, int(settings.line_width))
    r, g, b, a = settings.color[0], settings.color[1], settings.color[2], settings.color[3]

    half: int = line_w // 2
    y_start: int = max(pixel_y - half, 0)
    y_end: int = min(pixel_y - half + line_w, img_h)
    if y_start >= img_h or y_end <= 0:
        return None

    # Reuse or create image
    img = bpy.data.images.get(_OVERLAY_IMG_NAME)
    if img is not None and (img.size[0] != img_w or img.size[1] != img_h):
        bpy.data.images.remove(img)
        img = None
    if img is None:
        img = bpy.data.images.new(_OVERLAY_IMG_NAME, img_w, img_h,
                                  alpha=True, float_buffer=True)
        img.colorspace_settings.name = 'Linear Rec.709'

    # Fill pixels — numpy fast path with pure-Python fallback
    try:
        import numpy as np
        px = np.zeros((img_h, img_w, 4), dtype=np.float32)
        px[y_start:y_end, :] = [r, g, b, a]
        img.pixels.foreach_set(px.ravel())
    except Exception:
        px = [0.0] * (img_w * img_h * 4)
        for y in range(y_start, y_end):
            for x in range(img_w):
                idx = (y * img_w + x) * 4
                px[idx] = r
                px[idx + 1] = g
                px[idx + 2] = b
                px[idx + 3] = a
        img.pixels.foreach_set(px)

    img.update()
    return img


def _inject_compositor_nodes(scene: bpy.types.Scene,
                             overlay_img: bpy.types.Image) -> bool:
    """Insert Alpha Over + Image nodes before the Composite output.

    Returns ``True`` on success.
    """
    global _comp_cleanup

    use_nodes_was: bool = scene.use_nodes
    if not scene.use_nodes:
        scene.use_nodes = True

    tree = scene.node_tree

    # Find Composite output node (create if missing)
    comp_node = None
    for node in tree.nodes:
        if node.type == 'COMPOSITE':
            comp_node = node
            break
    if comp_node is None:
        comp_node = tree.nodes.new('CompositorNodeComposite')

    # Find the socket currently feeding the Composite/Image input
    original_from = None
    for link in tree.links:
        if link.to_node == comp_node and link.to_socket == comp_node.inputs['Image']:
            original_from = link.from_socket
            tree.links.remove(link)
            break

    # Fallback: look for a Render Layers node
    if original_from is None:
        for node in tree.nodes:
            if node.type == 'R_LAYERS':
                original_from = node.outputs['Image']
                break
    if original_from is None:
        return False

    # Image node → our overlay
    img_node = tree.nodes.new('CompositorNodeImage')
    img_node.name = _OVERLAY_NODE_IMG
    img_node.label = "EL View Overlay"
    img_node.image = overlay_img
    img_node.location = (comp_node.location.x - 400, comp_node.location.y - 200)

    # Alpha Over node
    alpha_node = tree.nodes.new('CompositorNodeAlphaOver')
    alpha_node.name = _OVERLAY_NODE_MIX
    alpha_node.label = "EL View Mix"
    alpha_node.location = (comp_node.location.x - 200, comp_node.location.y)

    # Wire:  render → AlphaOver(bg) ;  overlay → AlphaOver(fg) ;  AlphaOver → Composite
    tree.links.new(original_from, alpha_node.inputs[1])
    tree.links.new(img_node.outputs['Image'], alpha_node.inputs[2])
    tree.links.new(alpha_node.outputs['Image'], comp_node.inputs['Image'])

    _comp_cleanup = {
        'use_nodes_was': use_nodes_was,
        'original_from': original_from,
        'comp_node': comp_node,
    }
    return True


def _teardown_compositor(scene: bpy.types.Scene) -> None:
    """Remove temporary nodes / image and restore the original compositor."""
    global _comp_cleanup

    if _comp_cleanup is None:
        return

    info = _comp_cleanup
    _comp_cleanup = None

    tree = scene.node_tree
    if tree is None:
        return

    # Remove our nodes
    for name in (_OVERLAY_NODE_MIX, _OVERLAY_NODE_IMG):
        node = tree.nodes.get(name)
        if node is not None:
            tree.nodes.remove(node)

    # Restore original link
    try:
        tree.links.new(info['original_from'], info['comp_node'].inputs['Image'])
    except Exception:
        pass

    # Restore use_nodes flag
    if not info['use_nodes_was']:
        scene.use_nodes = False

    # Remove temp image
    img = bpy.data.images.get(_OVERLAY_IMG_NAME)
    if img is not None:
        bpy.data.images.remove(img)


# ---- render handlers ----

def _on_render_pre(scene, *_args) -> None:
    """Before each frame: create / update overlay and inject compositor nodes."""
    settings = getattr(scene, "elview_settings", None)
    if settings is None or not settings.enable or not settings.render_overlay:
        return

    ndc_y = _calc_eye_level_ndc_y_for_render(scene)
    if ndc_y is None:
        return

    overlay = _create_overlay_image(scene, ndc_y)
    if overlay is None:
        return

    if _comp_cleanup is None:
        _inject_compositor_nodes(scene, overlay)
    else:
        # Animation: just update the Image node's image reference
        tree = scene.node_tree
        if tree is not None:
            node = tree.nodes.get(_OVERLAY_NODE_IMG)
            if node is not None:
                node.image = overlay


def _on_render_complete(scene, *_args) -> None:
    """After render finishes: clean up compositor."""
    _teardown_compositor(scene)


def _on_render_cancel(scene, *_args) -> None:
    """If render is cancelled: clean up compositor."""
    _teardown_compositor(scene)


# ---------------------------------------------------------------------------
# UI Panel
# ---------------------------------------------------------------------------

class VIEW3D_PT_elview(Panel):
    """EL View settings panel in the 3D Viewport sidebar."""

    bl_label = "EL View"
    bl_idname = "VIEW3D_PT_elview"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "View"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        scene = context.scene
        settings = scene.elview_settings

        layout.prop(settings, "enable")

        col = layout.column()
        col.active = settings.enable
        col.prop(settings, "color")
        col.prop(settings, "line_width")
        col.separator()
        col.prop(settings, "render_overlay")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_classes = (
    ELViewSettings,
    VIEW3D_PT_elview,
)


def register() -> None:
    """Register the addon classes, properties, and handlers."""
    global _draw_handle

    for cls in _classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.elview_settings = PointerProperty(type=ELViewSettings)

    _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
        _draw_callback, (), 'WINDOW', 'POST_PIXEL'
    )

    bpy.app.handlers.render_pre.append(_on_render_pre)
    bpy.app.handlers.render_complete.append(_on_render_complete)
    bpy.app.handlers.render_cancel.append(_on_render_cancel)


def unregister() -> None:
    """Unregister the addon classes, properties, and handlers."""
    global _draw_handle

    if _on_render_cancel in bpy.app.handlers.render_cancel:
        bpy.app.handlers.render_cancel.remove(_on_render_cancel)
    if _on_render_complete in bpy.app.handlers.render_complete:
        bpy.app.handlers.render_complete.remove(_on_render_complete)
    if _on_render_pre in bpy.app.handlers.render_pre:
        bpy.app.handlers.render_pre.remove(_on_render_pre)

    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None

    del bpy.types.Scene.elview_settings

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
