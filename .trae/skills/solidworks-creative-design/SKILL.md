---
name: "solidworks-creative-design"
description: "Idea → browser 3D/physics preview → auto engineering drawing → SolidWorks model via Python COM. Invoke when a creative product or part must be modeled in SolidWorks but no detailed drawings exist."
---

# SolidWorks 创意设计流水线

把「一句话想法 + 需求」变成可编辑的 SolidWorks 零件，中间不依赖任何现成图纸。

核心思路：**用一份 `spec.json` 作为唯一契约**。人/agent 只写参数化意图，预览器与 SW 构建器都读同一份 spec，
所以图纸不是前提条件，而是产物。

```
想法/需求
   ↓ ①访谈     → 设计简报（尺寸约束、使用场景、工艺、预算）
   ↓ ②发散     → 3 个方向的概念簇（形态 × 结构 × 材料）→ 用户点选
   ↓ ③建模意图 → spec.json（特征树 + 参数 + 关节 + 校验规则）
   ↓ ④预演     → viewer.html：造型 / 物理 / 图纸 三模式，跑浏览器逐轮收敛   ← 这里的“图纸”由 spec 反推
   ↓ ⑤落地     → sw_build.py 用 Python COM 在 SolidWorks 里重放特征树，出 SLDPRT/STEP/STL
   ↓ ⑥校核     → 回读质量属性、干涉、壁厚，写 report.json，改参数回到 ③
```

## 何时使用

- 用户说「我有个想法，帮我设计个 XX 并出模型/图纸」
- 已有 agent 接入了 SolidWorks，但卡在「没有详细图纸就建不出模型」
- 需要快速做系列化变体（改参数出 N 个规格）
- 需要判断一个概念在物理上是否成立（会不会翻、会不会干涉、多重）

不适用：纯逆向抄图、纯 2D 工程图标注、非参数化的自由曲面雕塑造型。

## 快速开始

```bash
# 0) 把 skill 资产复制到项目里（不要直接改 skill 目录）
cp -r <SKILL_DIR>/assets/{viewer.html,spec-core.js,sw_build.py} ./cad/
cp <SKILL_DIR>/assets/example_spec.json ./cad/spec.json

# 1) 改 spec 后先做静态校验（任何系统都能跑）
python cad/sw_build.py cad/spec.json --check
python cad/sw_build.py cad/spec.json --plan      # 打印将要生成的特征树

# 2) 浏览器预演（agent 用浏览器工具打开并截图迭代）
cd cad && python3 -m http.server 8123
# http://localhost:8123/viewer.html?spec=./spec.json

# 3) 真正建模（Windows + SolidWorks + pywin32）
python cad/sw_build.py cad/spec.json --out ./build --drawing
```

## 六个阶段与门禁

### ① 访谈：补全「不可推导」的信息

只问 spec 里写不出来的东西，最多 5 个问题（详见 `references/01-intake.md`）：

1. 使用场景/用户/放置方式（决定包围盒与稳定性要求）
2. 载荷与安全系数（决定壁厚、材料）
3. 制造工艺：3D 打印 / 注塑 / CNC / 钣金（决定拔模、圆角、最小壁厚）
4. 成本/重量上限
5. 必须保留的既有件（螺丝规格、接口、装配关系）

> 门禁：写出一段 ≤3 行的设计简报 + 一张参数表（名称/初值/范围/单位）。信息不足时**给出默认值并标注假设**，不要反复追问。

### ② 发散：给 3 个方向，不要给 1 个

按 `references/02-ideation.md` 的三轴矩阵（形态 / 结构 / 材料-工艺）各取一个组合，
每个方向输出：一句话概念 + 关键差异点 + 预估尺寸/重量 + 风险。用 3 个缩略规格（或 3 份 spec 片段）让用户选。

> 门禁：用户选定 1 个方向，或明确说「你自己定」。选定后**冻结方向**，后续只调参数。

### ③ 写 spec：特征树必须是 SW 能重放的

一份 spec = 参数 + 零件 + 有序特征 + 关节 + 校验阈值。字段定义见 `references/03-spec-contract.md`。
硬性要求：

- 每个特征必须是 `草图(平面/平面偏移 + 2D 轮廓) → 拉伸/切除` 的形式，这是 SW 里最稳的可自动化特征。
- 尺寸能写成表达式就写表达式（`"$plate_w - 18"`），差值/壁厚由表达式保证，改一个参数全模型联动。
- 先减重槽、后开孔；切除特征放在被切零件之后。
- 关节只写 `revolute`，并给 `range` 与 `rest`。

> 门禁：`--check` 零 error、`--plan` 的特征顺序符合加工直觉。

### ④ 预演：在浏览器里把问题暴露出来

打开 `viewer.html`，三个模式都要跑一遍（操作手册见 `references/04-preview-and-drawing.md`）：

| 模式 | 看什么 | 判定标准 |
|---|---|---|
| 造型 | 比例、体块感、倒角是否够 | 一眼能读出功能；无悬空薄片 |
| 物理 | 关节扫掠角、重力力矩、AABB 间隙、倾覆角 | 间隙 > `checks.minClearance`；倾覆角 > 18° |
| 图纸 | 三视图、总尺寸、比例、标题栏 | 轮廓无漏画；尺寸链自洽 |

发现问题就改 spec 参数并 **重跑同一份文件**，不要手改几何。

> 门禁：三模式均无红色告警，或告警已被记录为「已知限制」。

### ⑤ 落地：SolidWorks 重放特征树

`sw_build.py` 读 spec → 建参考平面 → 画轮廓 → 拉伸/切除 → 圆角 → 存 SLDPRT → 导出 STEP/STL → 生成 report.json。
调用细节与常见坑见 `references/05-solidworks-api.md`。

> 门禁：report 里 `failed` 为空；若不为空，按报错逐条修 spec 或改 API 签名，**不要**手搓模型后宣称完成。

### ⑥ 校核：让数字说话

- 对比 report 里的质量属性与预演估算质量，偏差 > 25% 说明体积算法或特征没生效。
- 装配体用 SW 的「干涉检查」复核预演给出的 AABB 间隙结论。
- 交付前过一遍 `references/06-dfm-and-qa.md` 的清单。

## 建模坐标约定（跨工具统一）

spec 坐标 = SolidWorks 模型坐标：**X 右 / Y 上 / Z 朝观察者**，单位 mm，API 内部换算为 m。

| plane | SW 基准面 | 法向 n | 局部 u | 局部 v | offset 的含义 |
|---|---|---|---|---|---|
| `front` | Front Plane | +Z | +X | +Y | 距 YZ 平面的距离（沿 Z） |
| `top` | Top Plane | +Y | +X | −Z | 高度（沿 Y） |
| `right` | Right Plane | +X | −Z | +Y | 距 YZ 平面的距离（沿 X） |

`extrude.dir: "up"` = 沿 +n，`"down"` = 沿 −n。
轮廓 2D 坐标以草图局部 (u, v) 表达；`top` 平面的 v 轴朝 −Z，所以 `center:[0,31]` 得到 Z=−31。

## spec 最小骨架

```json
{
  "meta": { "name": "示例", "units": "mm", "material": "PETG", "process": "3d-print", "density": 1.27, "accent": "#5eead4" },
  "params": [{ "key": "w", "label": "宽度", "value": 80, "min": 50, "max": 120, "unit": "mm" }],
  "parts": [{ "id": "base", "label": "主体" }],
  "features": [
    { "id": "body", "part": "base", "op": "add",
      "sketch": { "plane": "top", "offset": 0, "profile": { "kind": "rect", "w": "$w", "h": 60, "cr": 8 } },
      "extrude": { "depth": 6, "dir": "up", "draft": 1.0 } },
    { "id": "hole", "part": "base", "op": "cut",
      "sketch": { "plane": "top", "offset": 6.3, "profile": { "kind": "circle", "d": 12, "center": [0, 0] } },
      "extrude": { "depth": 7, "dir": "down" } }
  ],
  "joints": [],
  "checks": { "minWall": 1.6, "minCorner": 1.0, "minClearance": 0.4 }
}
```

## 硬规则

1. **先 spec，后几何**。任何几何调整都要落回 spec 的参数/特征，不接受「只在 SW 里手拖」的交付。
2. **不要编造尺寸**。没依据的尺寸写进 `meta.assumptions`（可加字段），让用户能复核。
3. **切除特征的 `offset` 要比被切表面多 0.2–0.3mm**，否则 SW 里会出现零厚度几何/重建失败。
4. **每个特征一个 id**，命名用 `零件_功能`（`base_pocket`、`arm_slot`），report 以此对账。
5. **失败要显性化**。`failed` 非空时必须报告，不能静默通过。
6. **不覆盖用户已有工程**。输出到独立 `build/` 目录，文件名带时间戳。

## 失败回退

| 症状 | 处理 |
|---|---|
| viewer 打不开 / 白屏 | 用 `python3 -m http.server` 起服务，别用 `file://`；检查控制台是否 CDN 被墙，必要时把 three.js 下载到本地并改 importmap |
| `spec-core.js` 报「未知参数」 | 检查 `$param` 是否在 `params` 里声明 |
| SW 里拉伸方向反了 | 翻转 `extrude.dir`，或在 `PLANE_FRAMES`/`FRAMES` 对齐后再试 |
| 参考平面创建失败 | 偏移量改成正数 + 反方向法；或把该特征挪到基准面上用草图内偏移 |
| 某版本 API 名不存在 | `05-solidworks-api.md` 里有 `FeatureExtrusion2/3`、`FeatureCut2/3/4` 的降级顺序 |
| 圆角失败 | 跳过圆角，先保证主体；在 SW 里手工补 R，并记录到 report |

## 文件地图

```
solidworks-creative-design/
├── SKILL.md
├── assets/
│   ├── viewer.html          # 三模式预览器（造型/物理/图纸），读 spec.json
│   ├── spec-core.js         # 纯几何内核：spec → three.js 几何 + 指标（可被 node 单测）
│   ├── sw_build.py          # spec → SolidWorks（Python COM），支持 --check/--plan
│   └── example_spec.json    # 完整示例：折叠平板支架
└── references/
    ├── 01-intake.md         # 访谈清单 + 简报模板
    ├── 02-ideation.md       # 三方向发散 + 评分表
    ├── 03-spec-contract.md  # spec 字段全表 + 表达式规则
    ├── 04-preview-and-drawing.md  # 浏览器预演、指标解读、出图规范
    ├── 05-solidworks-api.md # SW API 食谱 + 报错排查
    └── 06-dfm-and-qa.md     # 工艺规则 + 交付自检清单
```
