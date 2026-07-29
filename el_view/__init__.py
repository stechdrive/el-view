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
    "version": (2, 1, 1),
    "blender": (4, 2, 0),
    "location": "3D View > N Panel > View > EL View",
    "description": "Display eye level (horizon) line on camera view and render output",
    "category": "3D View",
    "doc_url": "https://github.com/stechdrive/el-view",
}

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from bpy.app.handlers import persistent
from bpy.types import PropertyGroup, Panel
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty, PointerProperty
from mathutils import Vector
from typing import Optional, Tuple

Line2D = Tuple[Tuple[float, float], Tuple[float, float]]

# ---------------------------------------------------------------------------
# Property Group
# ---------------------------------------------------------------------------

def _settings_update(_self, context) -> None:
    """Keep the render compositor ready when an EL View setting changes."""
    sync = globals().get("_sync_scene_overlay")
    scene = getattr(context, "scene", None) if context is not None else None
    if sync is not None and scene is not None:
        sync(scene)


class ELViewSettings(PropertyGroup):
    """Settings for the eye level line overlay."""

    enable: BoolProperty(
        name="Enable",
        default=False,
        description="Show eye level line in camera view",
        update=_settings_update,
    )
    color: FloatVectorProperty(
        name="Color",
        subtype='COLOR',
        size=4,
        default=(1.0, 0.0, 0.0, 0.8),
        min=0.0,
        max=1.0,
        description="Line color and opacity",
        update=_settings_update,
    )
    line_width: FloatProperty(
        name="Line Width",
        default=2.0,
        min=1.0,
        max=10.0,
        description="Line thickness in pixels",
        update=_settings_update,
    )
    render_overlay: BoolProperty(
        name="Render Overlay",
        default=True,
        description="Composite eye level line onto render output",
        update=_settings_update,
    )


# ---------------------------------------------------------------------------
# Core calculation
# ---------------------------------------------------------------------------

def _get_eye_level_samples(
        cam_obj: bpy.types.Object) -> Optional[Tuple[Vector, Vector]]:
    """Return two world-space points spanning the eye-level plane.

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

    center: Vector = cam_pos + forward_xy * 100.0
    center.z = cam_pos.z  # eye level = camera world Z
    horizontal = Vector((-forward_xy.y, forward_xy.x, 0.0)) * 100.0
    return center - horizontal, center + horizontal


def _clip_infinite_line_to_rect(
        line: Line2D, max_x: float, max_y: float) -> Optional[Line2D]:
    """Clip an infinite 2D line to a rectangle from (0, 0) to (max_x, max_y)."""
    if max_x < 0.0 or max_y < 0.0:
        return None

    (x0, y0), (x1, y1) = line
    dx = x1 - x0
    dy = y1 - y0
    if (dx * dx) + (dy * dy) < 1e-12:
        return None

    epsilon = 1e-6
    candidates: list[Tuple[float, float]] = []

    def add_candidate(x: float, y: float) -> None:
        if (-epsilon <= x <= max_x + epsilon and
                -epsilon <= y <= max_y + epsilon):
            point = (
                min(max(x, 0.0), max_x),
                min(max(y, 0.0), max_y),
            )
            if all(
                    (point[0] - other[0]) ** 2 +
                    (point[1] - other[1]) ** 2 > 1e-10
                    for other in candidates):
                candidates.append(point)

    if abs(dx) > epsilon:
        for x in (0.0, max_x):
            t = (x - x0) / dx
            add_candidate(x, y0 + t * dy)
    if abs(dy) > epsilon:
        for y in (0.0, max_y):
            t = (y - y0) / dy
            add_candidate(x0 + t * dx, y)

    if len(candidates) < 2:
        return None

    # A corner intersection can produce more than two candidates after
    # floating-point clamping. Keep the pair spanning the longest segment.
    best_line: Optional[Line2D] = None
    best_distance = -1.0
    for index, start in enumerate(candidates):
        for end in candidates[index + 1:]:
            distance = (
                (end[0] - start[0]) ** 2 +
                (end[1] - start[1]) ** 2
            )
            if distance > best_distance:
                best_distance = distance
                best_line = (start, end)
    return best_line


def _calc_eye_level_ndc_line_for_render(
        scene: bpy.types.Scene) -> Optional[Line2D]:
    """Calculate the NDC line of the eye level for render output.

    Uses ``view_frame()`` and simple perspective math to compute the
    two-dimensional line without requiring a depsgraph. This works reliably
    in render handler contexts where ``bpy.context`` is restricted.
    """
    cam_obj = scene.camera
    if cam_obj is None:
        return None

    samples = _get_eye_level_samples(cam_obj)
    if samples is None:
        return None

    cam_data = cam_obj.data
    if cam_data.type not in {'PERSP', 'ORTHO'}:
        return None

    # view_frame returns the 4 frustum corners in camera local space.
    # No depsgraph needed — only camera data and scene render settings.
    frame = cam_data.view_frame(scene=scene)

    # Frustum depth (all corners share the same local Z)
    depth: float = -frame[0].z
    if depth <= 0.0:
        return None

    # Extents of the view plane
    xs = [co.x for co in frame]
    ys = [co.y for co in frame]
    left_x: float = min(xs)
    right_x: float = max(xs)
    bottom_y: float = min(ys)
    top_y: float = max(ys)
    frame_width: float = right_x - left_x
    frame_height: float = top_y - bottom_y
    if frame_width <= 0.0 or frame_height <= 0.0:
        return None

    world_to_camera = cam_obj.matrix_world.inverted()

    def project(sample: Vector) -> Optional[Tuple[float, float]]:
        sample_local: Vector = world_to_camera @ sample
        if cam_data.type == 'PERSP':
            if sample_local.z >= 0.0:
                return None
            scale = depth / (-sample_local.z)
            proj_x = sample_local.x * scale
            proj_y = sample_local.y * scale
        else:
            proj_x = sample_local.x
            proj_y = sample_local.y

        return (
            (2.0 * (proj_x - left_x) / frame_width) - 1.0,
            (2.0 * (proj_y - bottom_y) / frame_height) - 1.0,
        )

    start = project(samples[0])
    end = project(samples[1])
    if start is None or end is None:
        return None
    if ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) < 1e-12:
        return None
    return start, end


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

    samples = _get_eye_level_samples(cam_obj)
    if samples is None:
        return

    # Project two points so camera roll remains represented by the line slope.
    # location_3d_to_region_2d handles all viewport/camera/NDC mapping
    # correctly, including letterboxing and camera zoom/offset.
    projected = [
        location_3d_to_region_2d(region, rv3d, sample)
        for sample in samples
    ]
    if any(point is None for point in projected):
        return

    coords = _clip_infinite_line_to_rect(
        (
            (projected[0].x, projected[0].y),
            (projected[1].x, projected[1].y),
        ),
        float(region.width),
        float(region.height),
    )
    if coords is None:
        return

    color = tuple(settings.color)
    width: float = settings.line_width

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
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
# Instead, we inject an Image + Alpha Over node into the compositor tree.
# Blender 5.x compiles that tree before render handlers run, so the runtime
# nodes stay prepared while Render Overlay is enabled.  They are removed and
# the user's original links are restored when the option/add-on is disabled.
# ---------------------------------------------------------------------------

_OVERLAY_IMG_NAME: str = "__elview_overlay__"
_OVERLAY_NODE_IMG: str = "__elview_img_node__"
_OVERLAY_NODE_MIX: str = "__elview_alpha_node__"
_RUNTIME_TREE_PROP: str = "_elview_runtime_tree"
_RUNTIME_NODE_PROP: str = "_elview_runtime_created"
_RUNTIME_INTERFACE_PROP: str = "_elview_runtime_interface_socket"
_USE_NODES_WAS_OFF_PROP: str = "_elview_use_nodes_was_off"

# Runtime state used to restore each scene's compositor when EL View is
# disabled or the add-on is unloaded.
_comp_cleanups: dict[int, dict] = {}


def _scene_key(scene: bpy.types.Scene) -> int:
    return scene.as_pointer()


def _get_compositor_cleanup(scene: bpy.types.Scene) -> Optional[dict]:
    return _comp_cleanups.get(_scene_key(scene))


def _overlay_image_name(scene: bpy.types.Scene) -> str:
    return f"{_OVERLAY_IMG_NAME}_{_scene_key(scene):x}"


def _remove_runtime_images(images) -> None:
    for image in images:
        if image is not None and image.name.startswith(_OVERLAY_IMG_NAME):
            try:
                bpy.data.images.remove(image)
            except Exception:
                pass


def _remove_saved_runtime_nodes(tree: bpy.types.NodeTree) -> None:
    """Remove EL View nodes loaded from a saved file and restore their links."""
    image_nodes = [
        node for node in tree.nodes
        if node.name.startswith(_OVERLAY_NODE_IMG) or node.label == "EL View Overlay"
    ]
    alpha_nodes = [
        node for node in tree.nodes
        if node.name.startswith(_OVERLAY_NODE_MIX) or node.label == "EL View Mix"
    ]
    images = [node.image for node in image_nodes if hasattr(node, "image")]

    for alpha_node in alpha_nodes:
        original_from = None
        for input_socket in alpha_node.inputs:
            for link in input_socket.links:
                if link.from_node not in image_nodes:
                    original_from = link.from_socket
                    break
            if original_from is not None:
                break
        destinations = [
            link.to_socket
            for output_socket in alpha_node.outputs
            for link in output_socket.links
        ]
        try:
            tree.nodes.remove(alpha_node)
        except Exception:
            continue
        if original_from is not None:
            for destination in destinations:
                try:
                    tree.links.new(original_from, destination)
                except Exception:
                    pass

    for image_node in image_nodes:
        try:
            tree.nodes.remove(image_node)
        except Exception:
            pass

    for node in list(tree.nodes):
        if bool(node.get(_RUNTIME_NODE_PROP, False)):
            try:
                tree.nodes.remove(node)
            except Exception:
                pass
    interface_identifier = tree.get(_RUNTIME_INTERFACE_PROP, "")
    if interface_identifier and hasattr(tree, "interface"):
        for item in list(tree.interface.items_tree):
            if getattr(item, "identifier", "") == interface_identifier:
                try:
                    tree.interface.remove(item)
                except Exception:
                    pass
                break
        try:
            del tree[_RUNTIME_INTERFACE_PROP]
        except Exception:
            pass
    _remove_runtime_images(images)


def _remove_saved_runtime(scene: bpy.types.Scene,
                          restore_use_nodes: bool = False) -> None:
    """Clean runtime data that survived in a saved blend file."""
    if hasattr(scene, "compositing_node_group"):
        tree = scene.compositing_node_group
        if tree is not None and bool(tree.get(_RUNTIME_TREE_PROP, False)):
            images = [
                node.image for node in tree.nodes
                if node.type == 'IMAGE' and getattr(node, "image", None) is not None
            ]
            scene.compositing_node_group = None
            try:
                bpy.data.node_groups.remove(tree)
            except Exception:
                pass
            _remove_runtime_images(images)
        elif tree is not None:
            _remove_saved_runtime_nodes(tree)
    else:
        tree = getattr(scene, "node_tree", None)
        if tree is not None:
            _remove_saved_runtime_nodes(tree)

    if restore_use_nodes and bool(scene.get(_USE_NODES_WAS_OFF_PROP, False)):
        if hasattr(scene, "use_nodes"):
            scene.use_nodes = False
        try:
            del scene[_USE_NODES_WAS_OFF_PROP]
        except Exception:
            pass


def _ensure_compositor_tree(scene: bpy.types.Scene) -> Tuple[Optional[bpy.types.NodeTree], bool, bool]:
    """Return the scene compositor tree and how it must be restored.

    Blender 4.x owns the compositor tree directly on ``Scene.node_tree``.
    Blender 5.0 moved it to the assignable
    ``Scene.compositing_node_group`` data-block.  Feature detection keeps the
    same add-on package compatible with both APIs.
    """
    use_nodes_was = bool(getattr(scene, "use_nodes", False))
    if bool(scene.get(_USE_NODES_WAS_OFF_PROP, False)):
        use_nodes_was = False
    if hasattr(scene, "use_nodes") and not bool(scene.use_nodes):
        scene.use_nodes = True
    if not use_nodes_was:
        scene[_USE_NODES_WAS_OFF_PROP] = True

    if hasattr(scene, "compositing_node_group"):
        tree = scene.compositing_node_group
        created_tree = tree is None
        if created_tree:
            tree = bpy.data.node_groups.new(
                f"{scene.name} EL View Compositor", 'CompositorNodeTree'
            )
            tree[_RUNTIME_TREE_PROP] = True
            scene.compositing_node_group = tree
        return tree, use_nodes_was, created_tree

    return getattr(scene, "node_tree", None), use_nodes_was, False


def _ensure_compositor_output(tree: bpy.types.NodeTree,
                              use_group_output: bool) -> Tuple[
                                  bpy.types.Node,
                                  bpy.types.NodeSocket,
                                  bool,
                                  Optional[object],
                              ]:
    """Return the active final-output node and its image input socket."""
    created_output_node = False
    created_interface_socket = None

    if use_group_output:
        output_node = next(
            (
                node for node in tree.nodes
                if node.type == 'GROUP_OUTPUT' and getattr(node, "is_active_output", True)
            ),
            None,
        )
        if output_node is None:
            output_node = next(
                (node for node in tree.nodes if node.type == 'GROUP_OUTPUT'),
                None,
            )
        if output_node is None:
            output_node = tree.nodes.new('NodeGroupOutput')
            output_node[_RUNTIME_NODE_PROP] = True
            created_output_node = True

        output_input = output_node.inputs.get('Image')
        if output_input is None:
            created_interface_socket = tree.interface.new_socket(
                name='Image', in_out='OUTPUT', socket_type='NodeSocketColor'
            )
            tree[_RUNTIME_INTERFACE_PROP] = created_interface_socket.identifier
            output_input = output_node.inputs.get('Image')
    else:
        output_node = next(
            (node for node in tree.nodes if node.type == 'COMPOSITE'),
            None,
        )
        if output_node is None:
            output_node = tree.nodes.new('CompositorNodeComposite')
            output_node[_RUNTIME_NODE_PROP] = True
            created_output_node = True
        output_input = output_node.inputs.get('Image')

    if output_input is None:
        raise RuntimeError("EL View could not find the compositor image output")

    return output_node, output_input, created_output_node, created_interface_socket


def _rasterize_line_pixels(
        line: Line2D, width: int, height: int,
        line_width: int) -> list[Tuple[int, int]]:
    """Return image pixels covered by an infinite line."""
    if width <= 0 or height <= 0:
        return []

    clipped = _clip_infinite_line_to_rect(
        line, float(width - 1), float(height - 1)
    )
    if clipped is None:
        return []

    (x0, y0), (x1, y1) = clipped
    dx = x1 - x0
    dy = y1 - y0
    length = ((dx * dx) + (dy * dy)) ** 0.5
    if length < 1e-6:
        return []

    steps = max(2, int(max(abs(dx), abs(dy))) + 2)
    normal_x = -dy / length
    normal_y = dx / length
    width_count = max(1, line_width)
    pixels: set[Tuple[int, int]] = set()

    for step in range(steps):
        t = step / (steps - 1)
        center_x = x0 + dx * t
        center_y = y0 + dy * t
        for width_index in range(width_count):
            offset = width_index - ((width_count - 1) * 0.5)
            x = int(round(center_x + normal_x * offset))
            y = int(round(center_y + normal_y * offset))
            if 0 <= x < width and 0 <= y < height:
                pixels.add((x, y))

    return list(pixels)


def _create_overlay_image(
        scene: bpy.types.Scene, ndc_line: Line2D) -> Optional[bpy.types.Image]:
    """Create (or update) a transparent image with the eye-level line."""
    settings = scene.elview_settings
    render = scene.render
    scale: float = render.resolution_percentage / 100.0
    img_w: int = int(render.resolution_x * scale)
    img_h: int = int(render.resolution_y * scale)
    if img_w <= 0 or img_h <= 0:
        return None

    line_w: int = max(1, int(settings.line_width))
    pixel_line: Line2D = (
        (
            (ndc_line[0][0] + 1.0) * 0.5 * (img_w - 1),
            (ndc_line[0][1] + 1.0) * 0.5 * (img_h - 1),
        ),
        (
            (ndc_line[1][0] + 1.0) * 0.5 * (img_w - 1),
            (ndc_line[1][1] + 1.0) * 0.5 * (img_h - 1),
        ),
    )
    line_pixels = _rasterize_line_pixels(
        pixel_line, img_w, img_h, line_w
    )
    if not line_pixels:
        return None

    r, g, b, a = settings.color[0], settings.color[1], settings.color[2], settings.color[3]

    # Reuse or create image
    img_name = _overlay_image_name(scene)
    img = bpy.data.images.get(img_name)
    if img is not None and (img.size[0] != img_w or img.size[1] != img_h):
        bpy.data.images.remove(img)
        img = None
    if img is None:
        img = bpy.data.images.new(img_name, img_w, img_h,
                                  alpha=True, float_buffer=True)
        img.colorspace_settings.name = 'Linear Rec.709'

    # Fill pixels — numpy fast path with pure-Python fallback
    try:
        import numpy as np
        px = np.zeros((img_h, img_w, 4), dtype=np.float32)
        coordinates = np.asarray(line_pixels, dtype=np.intp)
        px[coordinates[:, 1], coordinates[:, 0]] = [r, g, b, a]
        img.pixels.foreach_set(px.ravel())
    except Exception:
        px = [0.0] * (img_w * img_h * 4)
        for x, y in line_pixels:
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
    existing = _get_compositor_cleanup(scene)
    if existing is not None:
        _teardown_compositor(scene)

    tree, use_nodes_was, created_tree = _ensure_compositor_tree(scene)
    if tree is None:
        if not use_nodes_was and hasattr(scene, "use_nodes"):
            scene.use_nodes = False
            try:
                del scene[_USE_NODES_WAS_OFF_PROP]
            except Exception:
                pass
        return False

    use_group_output = hasattr(scene, "compositing_node_group")
    info = {
        'scene': scene,
        'tree': tree,
        'use_nodes_was': use_nodes_was,
        'created_tree': created_tree,
        'created_output_node': False,
        'created_interface_socket': None,
        'created_render_layers_node': False,
        'output_node': None,
        'output_input': None,
        'original_from': None,
        'original_link_was_present': False,
        'img_node': None,
        'alpha_node': None,
        'overlay_img': overlay_img,
        'signature': None,
    }
    _comp_cleanups[_scene_key(scene)] = info

    try:
        output_node, output_input, created_output_node, created_interface_socket = (
            _ensure_compositor_output(tree, use_group_output)
        )
        info.update({
            'output_node': output_node,
            'output_input': output_input,
            'created_output_node': created_output_node,
            'created_interface_socket': created_interface_socket,
        })

        # Preserve exactly the link that was feeding the final image output.
        original_from = None
        for link in list(tree.links):
            if link.to_node == output_node and link.to_socket == output_input:
                original_from = link.from_socket
                info['original_from'] = original_from
                info['original_link_was_present'] = True
                tree.links.remove(link)
                break

        # A fresh or currently unlinked compositor still needs the render as
        # the background beneath the guide line.
        if original_from is None:
            render_layers = next(
                (node for node in tree.nodes if node.type == 'R_LAYERS'),
                None,
            )
            if render_layers is None:
                render_layers = tree.nodes.new('CompositorNodeRLayers')
                render_layers[_RUNTIME_NODE_PROP] = True
                info['created_render_layers_node'] = True
            original_from = render_layers.outputs.get('Image')
            info['render_layers_node'] = render_layers
        if original_from is None:
            raise RuntimeError("EL View could not find the rendered image socket")

        # Image node → our transparent guide image
        img_node = tree.nodes.new('CompositorNodeImage')
        img_node.name = _OVERLAY_NODE_IMG
        img_node.label = "EL View Overlay"
        img_node.image = overlay_img
        img_node.location = (output_node.location.x - 400, output_node.location.y - 200)
        info['img_node'] = img_node

        # Alpha Over node
        alpha_node = tree.nodes.new('CompositorNodeAlphaOver')
        alpha_node.name = _OVERLAY_NODE_MIX
        alpha_node.label = "EL View Mix"
        alpha_node.location = (output_node.location.x - 200, output_node.location.y)
        info['alpha_node'] = alpha_node

        background_input = alpha_node.inputs.get('Background') or alpha_node.inputs[1]
        foreground_input = alpha_node.inputs.get('Foreground') or alpha_node.inputs[2]

        # Wire: render → background; overlay → foreground; result → final output.
        tree.links.new(original_from, background_input)
        tree.links.new(img_node.outputs['Image'], foreground_input)
        tree.links.new(alpha_node.outputs['Image'], output_input)
        return True
    except Exception as exc:
        print(f"EL View: failed to inject compositor nodes: {exc}")
        _teardown_compositor(scene)
        return False


def _teardown_compositor(scene: bpy.types.Scene) -> None:
    """Remove temporary nodes / image and restore the original compositor."""
    info = _comp_cleanups.pop(_scene_key(scene), None)
    if info is None:
        return

    tree = info['tree']
    owner_scene = info['scene']

    if info['created_tree']:
        try:
            if owner_scene.compositing_node_group == tree:
                owner_scene.compositing_node_group = None
            bpy.data.node_groups.remove(tree)
        except Exception:
            pass
    else:
        # Removing the two temporary nodes also removes their temporary links.
        for key in ('alpha_node', 'img_node'):
            node = info.get(key)
            if node is not None:
                try:
                    tree.nodes.remove(node)
                except Exception:
                    pass

        # Restore only a link that existed before EL View touched the tree.
        if info['original_link_was_present']:
            try:
                tree.links.new(info['original_from'], info['output_input'])
            except Exception:
                pass

        if info.get('created_render_layers_node'):
            try:
                tree.nodes.remove(info['render_layers_node'])
            except Exception:
                pass
        if info.get('created_output_node'):
            try:
                tree.nodes.remove(info['output_node'])
            except Exception:
                pass
        interface_socket = info.get('created_interface_socket')
        if interface_socket is not None:
            try:
                tree.interface.remove(interface_socket)
            except Exception:
                pass

    # Restore the scene's compositor-enable state on both APIs.
    if not info['use_nodes_was'] and hasattr(owner_scene, "use_nodes"):
        owner_scene.use_nodes = False
        try:
            del owner_scene[_USE_NODES_WAS_OFF_PROP]
        except Exception:
            pass

    # Remove temp image
    img = info.get('overlay_img')
    if img is not None:
        try:
            bpy.data.images.remove(img)
        except Exception:
            pass


# ---- compositor synchronisation / render handlers ----

def _sync_scene_overlay(scene: bpy.types.Scene) -> None:
    """Prepare or update the persistent runtime compositor for *scene*.

    Blender 5.x compiles its compositor graph before ``render_init`` and
    ``render_pre`` run.  The graph therefore has to be ready while the scene is
    edited; the render handlers remain as a final per-frame value refresh.
    """
    settings = getattr(scene, "elview_settings", None)
    if settings is None or not settings.enable or not settings.render_overlay:
        if _get_compositor_cleanup(scene) is not None:
            _teardown_compositor(scene)
        else:
            _remove_saved_runtime(scene, restore_use_nodes=True)
        return

    ndc_line = _calc_eye_level_ndc_line_for_render(scene)
    if ndc_line is None:
        if _get_compositor_cleanup(scene) is not None:
            _teardown_compositor(scene)
        else:
            _remove_saved_runtime(scene, restore_use_nodes=True)
        return

    render = scene.render
    signature = (
        tuple(
            round(coordinate, 9)
            for point in ndc_line
            for coordinate in point
        ),
        render.resolution_x,
        render.resolution_y,
        render.resolution_percentage,
        round(settings.line_width, 4),
        tuple(round(value, 6) for value in settings.color),
    )
    info = _get_compositor_cleanup(scene)
    if info is not None and info.get('signature') == signature:
        return
    if info is None:
        _remove_saved_runtime(scene)

    overlay = _create_overlay_image(scene, ndc_line)
    if overlay is None:
        _teardown_compositor(scene)
        return

    if info is None:
        if not _inject_compositor_nodes(scene, overlay):
            try:
                bpy.data.images.remove(overlay)
            except Exception:
                pass
            return
        info = _get_compositor_cleanup(scene)
    else:
        node = info.get('img_node')
        if node is not None:
            node.image = overlay
        info['overlay_img'] = overlay

    if info is not None:
        info['signature'] = signature


@persistent
def _on_render_init(scene, *_args) -> None:
    """Refresh the graph before rendering (fallback for scripted workflows)."""
    _sync_scene_overlay(scene)


@persistent
def _on_render_pre(scene, *_args) -> None:
    """Refresh the overlay for the evaluated animation frame."""
    _sync_scene_overlay(scene)


@persistent
def _on_frame_change_post(scene, *_args) -> None:
    _sync_scene_overlay(scene)


@persistent
def _on_depsgraph_update_post(scene, *_args) -> None:
    _sync_scene_overlay(scene)


@persistent
def _on_load_post(_unused) -> None:
    for scene in bpy.data.scenes:
        _sync_scene_overlay(scene)


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
    bpy.app.handlers.render_init.append(_on_render_init)
    bpy.app.handlers.frame_change_post.append(_on_frame_change_post)
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update_post)
    bpy.app.handlers.load_post.append(_on_load_post)

    # Blender restricts bpy.data and bpy.context while an extension is being
    # registered. Files loaded afterward are synchronised by _on_load_post,
    # while scene changes use the property/depsgraph handlers.


def unregister() -> None:
    """Unregister the addon classes, properties, and handlers."""
    global _draw_handle

    for info in list(_comp_cleanups.values()):
        _teardown_compositor(info['scene'])
    for scene in bpy.data.scenes:
        _remove_saved_runtime(scene, restore_use_nodes=True)

    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    if _on_depsgraph_update_post in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update_post)
    if _on_frame_change_post in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_on_frame_change_post)
    if _on_render_init in bpy.app.handlers.render_init:
        bpy.app.handlers.render_init.remove(_on_render_init)
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
