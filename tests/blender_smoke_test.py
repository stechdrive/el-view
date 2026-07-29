"""Headless smoke test for add-on registration and Blender compositor APIs.

Run with, for example:
    blender --background --factory-startup --python tests/blender_smoke_test.py
"""

import importlib.util
import math
from pathlib import Path
import tempfile

import bpy
from mathutils import Quaternion

try:
    from _bpy_restrict_state import RestrictBlend
except ImportError:
    from bpy_restrict_state import RestrictBlend


ROOT = Path(__file__).resolve().parents[1]
ADDON_PATH = ROOT / "el_view" / "__init__.py"


def load_addon():
    spec = importlib.util.spec_from_file_location("el_view_smoke", ADDON_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_camera(scene):
    camera_data = bpy.data.cameras.new("EL View Test Camera")
    camera = bpy.data.objects.new("EL View Test Camera", camera_data)
    scene.collection.objects.link(camera)
    camera.rotation_euler = (math.pi / 2.0, 0.0, 0.0)
    scene.camera = camera
    bpy.context.view_layer.update()
    return camera


def make_existing_compositor(scene):
    """Create a minimal user-owned compositor and return its final sockets."""
    scene.use_nodes = True
    if hasattr(scene, "compositing_node_group"):
        tree = bpy.data.node_groups.new("EL View Existing Test", "CompositorNodeTree")
        scene.compositing_node_group = tree
        tree.interface.new_socket(
            name="Image", in_out='OUTPUT', socket_type='NodeSocketColor'
        )
        output = tree.nodes.new("NodeGroupOutput")
    else:
        tree = scene.node_tree
        tree.nodes.clear()
        output = tree.nodes.new("CompositorNodeComposite")

    layers = tree.nodes.new("CompositorNodeRLayers")
    tree.links.new(layers.outputs["Image"], output.inputs["Image"])
    return tree, layers.outputs["Image"], output.inputs["Image"]


def clear_compositor(scene, tree):
    if hasattr(scene, "compositing_node_group"):
        scene.compositing_node_group = None
        bpy.data.node_groups.remove(tree)
    else:
        tree.nodes.clear()
    # Blender 5.x decides whether compositing is enabled before render_init;
    # keep its default enabled state while testing late graph preparation.
    scene.use_nodes = hasattr(scene, "compositing_node_group")


def assert_red_center_line(image_path):
    image = bpy.data.images.load(str(image_path), check_existing=False)
    try:
        width, height = image.size
        pixels = [0.0] * (width * height * 4)
        image.pixels.foreach_get(pixels)
        samples = []
        for y in range(height // 2 - 3, height // 2 + 4):
            offset = (y * width + (width // 2)) * 4
            samples.append(pixels[offset:offset + 4])
        red, green, blue, alpha = max(
            samples, key=lambda rgba: rgba[0] - rgba[1] - rgba[2]
        )
        assert red > 0.8, (red, green, blue, alpha)
        assert red > green * 2.0 and red > blue * 2.0, (red, green, blue, alpha)
    finally:
        bpy.data.images.remove(image)


def assert_red_sloped_line(image_path, ndc_line):
    image = bpy.data.images.load(str(image_path), check_existing=False)
    try:
        width, height = image.size
        pixels = [0.0] * (width * height * 4)
        image.pixels.foreach_get(pixels)
        (x0, y0), (x1, y1) = ndc_line
        assert abs(x1 - x0) > 1e-6, ndc_line

        expected_ys = []
        for pixel_x in (width // 4, (width * 3) // 4):
            ndc_x = (2.0 * pixel_x / (width - 1)) - 1.0
            t = (ndc_x - x0) / (x1 - x0)
            ndc_y = y0 + (y1 - y0) * t
            pixel_y = (ndc_y + 1.0) * 0.5 * (height - 1)
            expected_ys.append(pixel_y)

            samples = []
            center_x = int(round(pixel_x))
            center_y = int(round(pixel_y))
            for y in range(max(0, center_y - 3), min(height, center_y + 4)):
                for x in range(max(0, center_x - 2), min(width, center_x + 3)):
                    offset = (y * width + x) * 4
                    samples.append(pixels[offset:offset + 4])
            red, green, blue, alpha = max(
                samples, key=lambda rgba: rgba[0] - rgba[1] - rgba[2]
            )
            assert red > 0.8, (
                pixel_x, pixel_y, red, green, blue, alpha, ndc_line
            )
            assert red > green * 2.0 and red > blue * 2.0, (
                pixel_x, pixel_y, red, green, blue, alpha, ndc_line
            )

        assert abs(expected_ys[1] - expected_ys[0]) > height * 0.15, (
            expected_ys, ndc_line
        )
    finally:
        bpy.data.images.remove(image)


def assert_image_transparent(image):
    pixels = [0.0] * (image.size[0] * image.size[1] * 4)
    image.pixels.foreach_get(pixels)
    assert max(pixels, default=0.0) == 0.0


def main():
    addon = load_addon()
    depsgraph_handlers_before = tuple(bpy.app.handlers.depsgraph_update_post)
    frame_handlers_before = tuple(bpy.app.handlers.frame_change_post)
    # Blender extensions are imported and registered with bpy.data/context
    # restricted. Keep this path covered so registration cannot accidentally
    # depend on the currently loaded blend file.
    with RestrictBlend():
        addon.register()
    try:
        # Camera/frame changes must never invoke compositor mutations.
        assert tuple(bpy.app.handlers.depsgraph_update_post) == depsgraph_handlers_before
        assert tuple(bpy.app.handlers.frame_change_post) == frame_handlers_before

        scene = bpy.context.scene
        make_camera(scene)
        scene.render.engine = 'BLENDER_WORKBENCH'
        scene.render.resolution_x = 64
        scene.render.resolution_y = 64
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = 'PNG'

        settings = scene.elview_settings
        settings.enable = False
        settings.render_overlay = True
        settings.color = (1.0, 0.0, 0.0, 1.0)
        settings.line_width = 2.0

        # EL View must leave an existing user compositor exactly connected.
        tree, original_from, original_to = make_existing_compositor(scene)
        node_count = len(tree.nodes)
        link_count = len(tree.links)
        overlay = addon._create_overlay_image(
            scene, ((-1.0, 0.0), (1.0, 0.0))
        )
        assert overlay is not None
        assert addon._inject_compositor_nodes(scene, overlay)
        assert len(tree.nodes) == node_count + 2
        # Simulate reopening a blend file: Python runtime state is gone, but
        # the prepared compositor nodes may have been saved in the file.
        addon._comp_cleanups.clear()
        addon._apply_scene_overlay_settings(scene)
        assert len(tree.nodes) == node_count
        assert len(tree.links) == link_count
        assert any(
            link.from_socket == original_from and link.to_socket == original_to
            for link in tree.links
        )

        # A scene without compositor nodes must render the guide and return to
        # a working runtime compositor before rendering.
        clear_compositor(scene, tree)
        settings.enable = True
        assert addon._get_compositor_cleanup(scene) is not None, (
            settings.enable,
            settings.render_overlay,
            addon._calc_eye_level_ndc_line_for_render(scene),
        )
        output_path = Path(tempfile.gettempdir()) / (
            f"el_view_smoke_{bpy.app.version[0]}_{bpy.app.version[1]}.png"
        )
        scene.render.filepath = str(output_path)
        bpy.ops.render.render(write_still=True)
        assert output_path.exists()
        assert_red_center_line(output_path)
        assert addon._get_compositor_cleanup(scene) is not None

        stable_info = addon._get_compositor_cleanup(scene)
        stable_tree = stable_info["tree"]
        stable_image = stable_info["overlay_img"]
        stable_signature = stable_info["signature"]
        stable_node_count = len(stable_tree.nodes)
        stable_link_count = len(stable_tree.links)

        # Rolling around the camera's viewing axis must tilt the world horizon
        # in both the shared projection and the rendered overlay. Camera and
        # frame changes must not touch compositor data before the next render.
        camera = scene.camera
        base_rotation = camera.rotation_euler.to_quaternion()
        camera.rotation_mode = 'QUATERNION'
        for step in range(101):
            roll = math.radians(30.0 * step / 100.0)
            camera.rotation_quaternion = (
                base_rotation @ Quaternion((0.0, 0.0, 1.0), roll)
            )
            scene.frame_set(1 + (step % 2))
            bpy.context.view_layer.update()

            current_info = addon._get_compositor_cleanup(scene)
            assert current_info is stable_info
            assert current_info["tree"] == stable_tree
            assert current_info["overlay_img"] == stable_image
            assert current_info["img_node"].image == stable_image
            assert current_info["signature"] == stable_signature
            assert len(stable_tree.nodes) == stable_node_count
            assert len(stable_tree.links) == stable_link_count

        ndc_line = addon._calc_eye_level_ndc_line_for_render(scene)
        assert ndc_line is not None
        assert abs(ndc_line[1][1] - ndc_line[0][1]) > 0.1, ndc_line

        rolled_output_path = Path(tempfile.gettempdir()) / (
            f"el_view_smoke_roll_{bpy.app.version[0]}_"
            f"{bpy.app.version[1]}.png"
        )
        scene.render.filepath = str(rolled_output_path)
        bpy.ops.render.render(write_still=True)
        assert rolled_output_path.exists()
        assert_red_sloped_line(rolled_output_path, ndc_line)
        assert addon._get_compositor_cleanup(scene) is stable_info
        assert stable_info["overlay_img"] == stable_image
        assert stable_info["img_node"].image == stable_image
        assert len(stable_tree.nodes) == stable_node_count
        assert len(stable_tree.links) == stable_link_count

        # With the camera pointing straight down, the horizon is undefined.
        # The graph and image stay in place while the image becomes transparent.
        camera.rotation_quaternion = Quaternion()
        bpy.context.view_layer.update()
        assert addon._calc_eye_level_ndc_line_for_render(scene) is None
        addon._update_render_overlay_for_render(scene)
        undefined_info = addon._get_compositor_cleanup(scene)
        assert undefined_info is stable_info
        assert undefined_info["tree"] == stable_tree
        assert undefined_info["overlay_img"] == stable_image
        assert undefined_info["img_node"].image == stable_image
        assert len(stable_tree.nodes) == stable_node_count
        assert len(stable_tree.links) == stable_link_count
        assert_image_transparent(stable_image)

        # A line outside the frame follows the same transparent-image path.
        assert addon._create_overlay_image(
            scene, ((-1.0, 2.0), (1.0, 2.0))
        ) == stable_image
        assert_image_transparent(stable_image)

        # Reopening an enabled file must replace, not duplicate, runtime nodes.
        addon._discard_runtime_state()
        addon._restore_runtime_state()
        reopened_info = addon._get_compositor_cleanup(scene)
        assert reopened_info is not None
        assert sum(
            node.label == "EL View Mix" for node in reopened_info["tree"].nodes
        ) == 1

        # Disabling EL View restores the scene state and removes runtime data.
        settings.enable = False
        assert addon._get_compositor_cleanup(scene) is None
        assert scene.use_nodes == hasattr(scene, "compositing_node_group")
        if hasattr(scene, "compositing_node_group"):
            assert scene.compositing_node_group is None
        else:
            assert len(scene.node_tree.nodes) == 0

        print(
            "EL_VIEW_SMOKE_OK",
            bpy.app.version_string,
            "new_compositor_api=" + str(hasattr(scene, "compositing_node_group")),
        )
    finally:
        addon.unregister()


if __name__ == "__main__":
    main()
