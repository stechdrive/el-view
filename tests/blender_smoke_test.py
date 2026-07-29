"""Headless smoke test for add-on registration and Blender compositor APIs.

Run with, for example:
    blender --background --factory-startup --python tests/blender_smoke_test.py
"""

import importlib.util
import math
from pathlib import Path
import tempfile

import bpy

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


def main():
    addon = load_addon()
    # Blender extensions are imported and registered with bpy.data/context
    # restricted. Keep this path covered so registration cannot accidentally
    # depend on the currently loaded blend file.
    with RestrictBlend():
        addon.register()
    try:
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
        overlay = addon._create_overlay_image(scene, 0.0)
        assert overlay is not None
        assert addon._inject_compositor_nodes(scene, overlay)
        assert len(tree.nodes) == node_count + 2
        # Simulate reopening a blend file: Python runtime state is gone, but
        # the prepared compositor nodes may have been saved in the file.
        addon._comp_cleanups.clear()
        addon._sync_scene_overlay(scene)
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
            addon._calc_eye_level_ndc_y_for_render(scene),
        )
        output_path = Path(tempfile.gettempdir()) / (
            f"el_view_smoke_{bpy.app.version[0]}_{bpy.app.version[1]}.png"
        )
        scene.render.filepath = str(output_path)
        bpy.ops.render.render(write_still=True)
        assert output_path.exists()
        assert_red_center_line(output_path)
        assert addon._get_compositor_cleanup(scene) is not None

        # Reopening an enabled file must replace, not duplicate, runtime nodes.
        addon._comp_cleanups.clear()
        addon._sync_scene_overlay(scene)
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
