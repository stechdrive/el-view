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
    "version": (1, 1, 0),
    "blender": (3, 6, 0),
    "location": "3D View > Nパネル > View > EL View",
    "description": "カメラビューにアイレベルガイドを描くオーバーレイ",
    "category": "3D View",
    "doc_url": "https://github.com/stechdrive/el-view",  # リポジトリ
}

import math
import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from bpy_extras.view3d_utils import location_3d_to_region_2d
from bpy.props import BoolProperty, FloatVectorProperty, FloatProperty

_draw_handle = None
_shader = None


def draw_eye_level():
    """3Dビューごとに呼ばれて、必要ならアイレベル線を描く"""
    global _shader

    context = bpy.context
    scene = context.scene

    # シーン側でOFFならなにもしない
    if not getattr(scene, "elview_enable", False):
        return

    area = context.area
    region = context.region
    space = context.space_data

    # 3Dビューのウィンドウ領域以外では描かない
    if not area or area.type != 'VIEW_3D':
        return
    if not region or region.type != 'WINDOW':
        return
    if not isinstance(space, bpy.types.SpaceView3D):
        return

    rv3d = space.region_3d

    # カメラビューでないときは描かない
    if rv3d.view_perspective != 'CAMERA':
        return

    # このビューで使っているカメラを取得
    cam_obj = space.camera if space.camera else scene.camera
    if cam_obj is None or cam_obj.type != 'CAMERA':
        return

    origin = cam_obj.matrix_world.translation
    # カメラの前方ベクトル（ローカル -Z）
    forward = (cam_obj.matrix_world.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()

    region = context.region
    rv3d = space.region_3d

    # ===== 数学的に安定した地平線近似 =====
    # 「地面」をワールドの XY 平面（Z が上）と仮定し、
    # XY平面上の方向ベクトルをぐるっとサンプリングして、
    # その投影の中で画面の最も左と右を結ぶ

    # 距離（ユーザーが調整可能）
    distance = getattr(scene, "elview_distance", 20000.0)

    # サンプリング本数
    num_dirs = 64  # 必要なら 32〜128 程度で調整

    points_2d = []

    for i in range(num_dirs):
        theta = 2.0 * math.pi * i / num_dirs
        # XY平面上の単位ベクトル
        d = Vector((math.cos(theta), math.sin(theta), 0.0))

        # カメラの前方側になるように向きを調整
        if d.dot(forward) < 0.0:
            d = -d

        world_point = origin + d * distance
        p2d = location_3d_to_region_2d(region, rv3d, world_point)
        if p2d is not None:
            points_2d.append(p2d)

    # 投影できた点が 2 つ未満なら描かない（極端に特殊な向き）
    if len(points_2d) < 2:
        return

    # 画面上で一番左と一番右の点を端点に採用
    left = min(points_2d, key=lambda p: p.x)
    right = max(points_2d, key=lambda p: p.x)
    coords = [(left.x, left.y), (right.x, right.y)]

    # シェーダ初期化（GPU リセットに備えて遅延生成）
    if _shader is None:
        _shader = gpu.shader.from_builtin('UNIFORM_COLOR')

    batch = batch_for_shader(_shader, 'LINES', {"pos": coords})
    color = getattr(scene, "elview_color", (1.0, 0.0, 0.0, 1.0))

    gpu.state.blend_set("ALPHA")
    gpu.state.line_width_set(2.0)

    _shader.bind()
    _shader.uniform_float("color", color)
    batch.draw(_shader)

    # あと片付け
    gpu.state.line_width_set(1.0)
    gpu.state.blend_set("NONE")


def update_eye_level(self, context):
    """ON/OFF切り替え時にドローハンドラを登録/解除"""
    global _draw_handle

    enable = context.scene.elview_enable

    if enable and _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            draw_eye_level, (), 'WINDOW', 'POST_PIXEL'
        )
    elif not enable and _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None


class VIEW3D_PT_elview_panel(bpy.types.Panel):
    """Nパネルに出す簡単なUI"""
    bl_label = "EL View"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "View"  # 独立タブにしたければ "EL View"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        col = layout.column()
        col.prop(scene, "elview_enable", text="Enable EL View")
        col.prop(scene, "elview_color", text="Color")
        col.prop(scene, "elview_distance", text="Distance")


classes = (
    VIEW3D_PT_elview_panel,
)


def register():
    from bpy.utils import register_class
    for cls in classes:
        register_class(cls)

    bpy.types.Scene.elview_enable = BoolProperty(
        name="EL View",
        description="カメラビューにアイレベル線を表示",
        default=False,
        update=update_eye_level,
    )

    bpy.types.Scene.elview_color = FloatVectorProperty(
        name="EL View Color",
        subtype='COLOR',
        size=4,
        min=0.0, max=1.0,
        default=(1.0, 0.2, 0.2, 1.0),
    )

    bpy.types.Scene.elview_distance = FloatProperty(
        name="Distance",
        description="アイレベルを計算するための距離（広角レンズで足りなければ増やす）",
        default=20000.0,
        min=1000.0,
        max=100000.0,
        soft_min=5000.0,
        soft_max=50000.0,
    )


def unregister():
    global _draw_handle
    from bpy.utils import unregister_class

    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove
