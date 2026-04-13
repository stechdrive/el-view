# EL View

Blender add-on that displays a stable eye-level (horizon) guide inside the camera view.

## 日本語

**EL View** は、Blender のカメラビュー上に「アイレベル（地平線）」ガイドを表示するアドオンです。

- カメラビューにアイレベルの水平線をリアルタイムオーバーレイ表示
- レンダリング画像（静止画・アニメーション）にもラインを合成可能
- 線の色・太さ・透明度をカスタマイズ
- レンダリングへの合成を個別に ON/OFF
- Eevee / Cycles / Workbench すべてのレンダリングエンジンに対応
- カメラがどの方向を向いていても安定して表示

### 動作要件

- Blender 4.0 以上

### インストール方法

1. Releases から `el_view.zip` を [Download](https://github.com/stechdrive/el-view/releases)
2. Blender → `Edit` → `Preferences` → `Add-ons`
3. 右上の `↓` ドロップダウン → `Install from Disk...`
4. ダウンロードした `el_view.zip` を選択
5. アドオン `EL View` を有効化

### 使い方

1. 3D ビューの N パネル → `View` タブ → `EL View` パネル
2. `Enable` にチェックを入れると、カメラビュー表示時にアイレベル線が表示される
3. `Color` で線の色と透明度を変更
4. `Line Width` で線の太さを調整（1〜10px）
5. `Render Overlay` のチェックでレンダリング出力への合成を ON/OFF

### アイレベルとは

カメラの高さ（ワールド Z 座標）と同じ高さの水平面が、カメラのフレーム内でどこに見えるかを示す線です。

- カメラが水平 → 画面中央付近
- 煽り（ローアングル） → 画面下方
- 俯瞰（ハイアングル） → 画面上方

---

## English

**EL View** is a Blender add-on that displays an accurate and stable eye-level (horizon) guide inside the camera view.

- Real-time eye-level line overlay in camera view
- Composites line onto render output (stills and animation)
- Customizable color, opacity, and line width
- Render overlay can be toggled independently
- Works with Eevee, Cycles, and Workbench
- Stable display regardless of camera orientation

### Requirements

- Blender 4.0+

### Installation

1. [Download](https://github.com/stechdrive/el-view/releases) `el_view.zip` from Releases
2. Blender → `Edit` → `Preferences` → `Add-ons`
3. Click `↓` dropdown → `Install from Disk...`
4. Select `el_view.zip`
5. Enable `EL View`

### Usage

1. Open N-panel in 3D View → `View` tab → `EL View` panel
2. Check `Enable` to show the eye-level line in camera view
3. Adjust `Color` for line color and opacity
4. Adjust `Line Width` (1–10px)
5. Toggle `Render Overlay` to composite on render output

---

## License

This project is licensed under the MIT License. See `LICENSE` for details.
