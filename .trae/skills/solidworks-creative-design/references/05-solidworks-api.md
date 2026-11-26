# 05 · SolidWorks API 食谱（Python COM）

`assets/sw_build.py` 的实现依据。只用了**最稳定的那一小撮 API**：参考平面 → 草图 → 拉伸/切除 → 保存/导出。
遇到版本差异时按下面的降级顺序改，不要重写整条链路。

## 环境

```bash
pip install pywin32
```

- Windows + SolidWorks 2018 及以上；建议先手动打开一次 SW 并接受所有弹窗（首次启动会卡住自动化）。
- `Dispatch("SldWorks.Application")` 会附着到**已运行**的实例，没有则启动新实例。
- `--visible` 时显示界面，调试阶段务必开；批量跑时可以不开。

## 单位：API 全是米

spec 是 mm，所有传进 API 的尺寸都要 `/1000`。
不要试图用「文档单位」来省这一步——`SetUserPreferenceInteger` 只影响界面显示，不改 API 入参。

## 主干

```python
import win32com.client as win32
sw = win32.Dispatch("SldWorks.Application")
sw.Visible = True
model = sw.NewDocument(sw.GetUserPreferenceStringValue(8), 0, 0, 0)  # 8 = 默认零件模板
```

`NewDocument` 返回 `None` 时，几乎都是**默认模板路径没配**：
工具 → 选项 → 系统选项 → 文件位置 → 文件模板。

## 1. 偏移参考平面

```python
model.ClearSelection2(True)
model.Extension.SelectByID2("Top Plane", "PLANE", 0, 0, 0, False, 0, None, 0)
feat = model.FeatureManager.InsertRefPlane(8, 0.008, 0, 0, 0, 0)  # 8 = 距离约束
```

- `InsertRefPlane(FirstConstraint, FirstVal, SecondConstraint, SecondVal, ThirdConstraint, ThirdVal)`
- `8` = 距离，`16` = 角度，`4` = 重合
- 负距离在部分版本会返回 `None`：翻成正数从反方向再建一次（脚本已内置这个回退）
- 基准面名在新版中文界面是 `前视基准面`/`上视基准面`/`右视基准面`；脚本用英文名，若你的 SW 是中文且报「选不中基准面」，
  改成中文名即可（或在 SW 里把基准面改名成英文）。

## 2. 草图

```python
model.ClearSelection2(True)
model.Extension.SelectByID2(plane_name, "PLANE", 0, 0, 0, False, 0, None, 0)
sm = model.SketchManager
sm.InsertSketch(True)          # 进入草图
sm.CreateLine(x1, y1, 0, x2, y2, 0)
sm.CreateCircleByRadius(cx, cy, 0, r)
sm.CreateArc(xc, yc, 0, x1, y1, 0, x2, y2, 0, 1)   # 最后一位 1=逆时针
sm.InsertSketch(True)          # 退出草图
```

坐标是**草图局部坐标**（u, v），z 传 0。平面上的位置由「基准面 + 参考平面偏移」决定，不要试图用 3D 坐标直接画。

圆角矩形 = 4 条直线 + 4 段 `CreateArc`，按逆时针顺序首尾相接，最后一点回到起点（无需再画闭合线）。

## 3. 拉伸 / 切除

```python
fm = model.FeatureManager
# 凸台：FeatureExtrusion3
feat = fm.FeatureExtrusion3(
    True,            # Sd
    False,           # Flip
    reverse,         # Dir   —— True 时反向
    0, 0,            # T1,T2 —— 0 = 盲孔（给定深度）
    d, d,            # D1,D2 (m)
    has_draft, 0,    # Dft1,Dft2 —— 是否拔模
    rad_draft, rad_draft,   # DAng1,DAng2 (弧度)
    False, False,    # OffsetReverse1/2
    False, False,    # TranslateSurface1/2
    True,            # Merge —— 与已有实体合并
    True, True,      # UseFeatScope, UseAutoSelect
    0, 0, False      # T0, StartOffset, FlipStartOffset
)

# 切除：FeatureCut4（前 16 个参数同上，之后是 NormalCut 等）
```

降级顺序（脚本已实现）：

- 凸台：`FeatureExtrusion3` → `FeatureExtrusion2`（18 参，去掉 T0/StartOffset/FlipStartOffset）
- 切除：`FeatureCut4` → `FeatureCut3` → `FeatureCut2`

返回 `None` 就是失败。常见原因：草图未闭合、草图里有多个分离轮廓、拉伸深度为 0、轮廓自交。

## 4. 圆角

```python
fm.FeatureFillet3(r, 0, 0, 0, 0, 0)
```

各版本签名不一致，脚本按 `FeatureFillet3 → FeatureFillet2 → FeatureFillet` 依次尝试，全失败就跳过并打日志。
更稳的做法：先在 SW 里录一个宏，把实际签名抄回来替换。

> 圆角必须**先选边**才能只倒需要的边。全自动时通常是整零件倒角，若某些边不允许倒（薄壁/相切），
> 该特征会整体失败——所以 `fillet` 只作为可选收尾，不是必须步骤。

## 5. 保存与导出

```python
model.SaveAs3(r"D:\out\part.SLDPRT", 0, 0)
model.SaveAs3(r"D:\out\part.step",  0, 0)   # 按扩展名自动选导出器
model.SaveAs3(r"D:\out\part.stl",   0, 0)
```

`SaveAs3(PathName, Version, Options)`，返回 `False`/`None` 视为失败。
导出 STEP 前确保模型已重建：`model.EditRebuild3()`（脚本在保存前隐式依赖 SW 的自动重建）。

## 6. 质量属性回读

```python
mp = model.Extension.GetMassProperties2(1, None, False)
# 返回 [重心X, 重心Y, 重心Z, 体积, 表面积, 质量, ...]  （米制）
```

失败就跳过——脚本本身会用「轮廓面积 × 深度 × 密度」再算一遍做交叉校验，两者差 >25% 说明有特征没生效。

## 7. 工程图（实验性）

```python
drw = sw.NewDocument(sw.GetUserPreferenceStringValue(10), 0, 0, 0)
drw.CreateDrawViewFromModelView3(model_path, "*Front", 0.10, 0.14, 0)
drw.CreateDrawViewFromModelView3(model_path, "*Top",   0.10, 0.05, 0)
drw.CreateDrawViewFromModelView3(model_path, "*Right", 0.28, 0.14, 0)
drw.InsertModelAnnotations3(0, 1 | 2 | 4 | 8, True, True, True, False)
```

- 视图名用 `*Front` / `*Top` / `*Right`，前面的 `*` 表示标准视图。
- 位置参数是**米**（图纸坐标），不是毫米。
- 注释导入依赖「文档属性 → 出详图」里的设置，常常导不出尺寸——这是常态，人工补。
- 工程图失败不影响模型，脚本会吞掉异常并记录。

## 排错表

| 报错 / 现象 | 原因 | 处理 |
|---|---|---|
| `Dispatch` 抛 `Invalid class string` | 没装 SW 或位数不匹配（64 位 python 配 32 位 SW） | 用 64 位 Python |
| `NewDocument` 返回 None | 默认零件模板未设置 | 选项 → 文件位置 → 文件模板 |
| `SelectByID2` 返回 False | 基准面名不符（中文界面）或对象被隐藏 | 改 `FRAMES` 里的 `sw` 名 |
| 参考平面建在错误一侧 | 偏移方向与法向相反 | 改 offset 符号；参考 03 的坐标表 |
| 拉伸方向反了 | `Dir` 语义不同 | 翻转 `extrude.dir` |
| 特征创建成功但位置错 | 平面选错（front/top/right 混淆） | `--plan` 对照特征树 |
| 切除做出零厚度 | 切除面与被切面共面 | offset 多加 0.2–0.3mm |
| 中文路径保存失败 | COM 对非 ASCII 路径偶发问题 | 输出目录用英文 |
| 连续跑多个零件卡死 | SW 弹窗阻塞（如「是否重建」） | 开 `--visible` 手动过一次，或关闭相关提示 |

## 调试流程

```bash
python sw_build.py spec.json --plan        # 先确认特征树
python sw_build.py spec.json --out ./build --visible
# 看 build/*_report.json 的 created / failed
```

`report.json` 结构：

```json
{
  "ok": false,
  "created": ["base_body", "base_pocket"],
  "failed": [{"id": "arm_slot", "error": "拉伸特征创建失败"}],
  "part": "./build/xxx.SLDPRT",
  "step": "./build/xxx.step",
  "stl": "./build/xxx.stl",
  "mass": {"raw": null},
  "auto_fillet": [{"id": "base_body", "radius": 12}]
}
```

`auto_fillet` 是提示：草图的 `cr` 不会自动变成圆角特征，它只是建议值，供你在 SW 里补或把它写进 spec 的 `fillet`。
