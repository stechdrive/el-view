# EL View

Blender add-on that displays a stable eye-level (horizon) guide inside the camera view.

## 日本語

**EL View** は、Blender のカメラビュー上に「アイレベル（地平線）」ガイドを正確かつ安定して表示するアドオンです。

- XY 平面（Z が上）の「地面」前提で地平線を計算  
- カメラの向きに依存しない安定したライン  
- サンプル・ベクトルを複数方向に投影して左右端を確実に取得  
- 色と距離パラメータを調整可能

インストール方法：

1. Releasesからel-view.zipをダウンロード  
2. Blender → *編集 > プリファレンス > アドオン > インストール*  
3. ZIP を選択  
4. アドオンを有効化  
5. 3Dビューの *N パネル → View → EL View* で操作

---

## English

**EL View** is a Blender add-on that displays an accurate and stable eye-level (horizon) guide inside the camera view.

- Assumes XY plane (Z-up) as ground  
- Computes horizon direction using multiple ray samples  
- Always produces a stable left–right line even when rotating camera  
- Adjustable color and sampling distance

Installation:

1. Download el-view.zip  
2. Blender → *Edit > Preferences > Add-ons > Install*  
3. Select the ZIP  
4. Enable the add-on  
5. Access controls in *N‑panel → View → EL View*

---

## License

This project is licensed under the MIT License. See `LICENSE` for details.

