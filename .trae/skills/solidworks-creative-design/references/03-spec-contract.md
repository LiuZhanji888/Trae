# 03 · spec.json 契约（字段全表）

spec 是唯一真相来源：`viewer.html`（预览）与 `sw_build.py`（建模）都只读它。
两边的解析规则必须一致，改动时同步 `assets/spec-core.js` 与 `assets/sw_build.py`。

## 顶层

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `meta` | object | ✓ | 名称、工艺、材料、密度、口述简报 |
| `params` | array | ✓ | 设计参数（带范围，供滑块与扫描） |
| `parts` | array | ✓ | 零件（装配体里的刚体） |
| `features` | array | ✓ | 有序特征树（顺序 = 建模顺序） |
| `joints` | array | | 零件间关节 |
| `checks` | object | | 校验阈值 |
| `fillet` | number | | 整零件统一圆角半径（mm），可省略 |

### meta

| 字段 | 说明 |
|---|---|
| `name` | 项目名，进标题栏与文件名 |
| `brief` | ≤3 行设计简报，显示在预览器侧栏 |
| `units` | 固定 `"mm"` |
| `material` | `PETG` / `PLA` / `ABS` / `PA-CF` / `AL6061` / `SUS304` … |
| `process` | `3d-print` / `sla` / `injection` / `cnc` / `sheet-metal` |
| `density` | g/cm³，用于质量估算（PETG 1.27、PLA 1.24、ABS 1.04、铝 2.70、钢 7.85） |
| `accent` | 预览器强调色，`#rrggbb` |
| `revision` | 版本号，进标题栏 |

### params[]

```json
{ "key": "plate_w", "label": "底座宽", "value": 92, "min": 70, "max": 130, "unit": "mm" }
```

`key` 只能用 `[A-Za-z_]\w*`；在别处引用写 `"$plate_w"`。

### parts[]

```json
{ "id": "base", "label": "底座" }
```

`id` 被 `features[].part` 与 `joints[].a/b` 引用。只写真正会相对运动的零件；
单件产品也至少要有一个 part（可只写 `base`）。

## features[]（核心）

```json
{
  "id": "base_body",
  "label": "底座主体",
  "part": "base",
  "op": "add",
  "sketch": {
    "plane": "top",
    "offset": 0,
    "profile": { "kind": "rect", "w": "$plate_w", "h": "$plate_d", "center": [0, 0], "cr": 12 }
  },
  "extrude": { "depth": "$plate_t", "dir": "up", "draft": 1.0 }
}
```

| 字段 | 取值 | 说明 |
|---|---|---|
| `id` | 唯一字符串 | 命名 `零件_功能`，report 用它对账 |
| `part` | parts 的 id | 省略则归第一个 part |
| `op` | `add` \| `cut` | 凸台 / 切除 |
| `sketch.plane` | `front` \| `top` \| `right` | 见下方坐标表 |
| `sketch.offset` | 数字/表达式 | 平面沿法向的偏移（mm） |
| `sketch.profile` | 见下 | 2D 轮廓 |
| `extrude.depth` | 数字/表达式 | 拉伸/切除深度（mm） |
| `extrude.dir` | `up` \| `down` | 沿 +法向 / −法向 |
| `extrude.draft` | 数字（度） | 拔模角，仅 `add` 有效，省略=0 |

### profile 三种

**rect**（可倒角，`cr` = 四角圆角半径）

```json
{ "kind": "rect", "w": 92, "h": 70, "center": [0, 0], "cr": 12 }
```

**circle**（`d` 或 `r` 二选一）

```json
{ "kind": "circle", "d": 4.4, "center": [-34, -24] }
```

**polygon**（闭合折线，逆时针给出）

```json
{ "kind": "polygon", "pts": [[0,0],[40,0],[40,20],[12,20],[12,50],[0,50]] }
```

> 不支持样条 / 椭圆 / 带孔的轮廓。要「板上有方孔」，用 `cut` 特征叠加，不要试图在轮廓里开洞。

## 坐标约定（预览与 SW 必须一致）

| plane | SW 基准面 | 法向 n | u 轴 | v 轴 | offset 方向 |
|---|---|---|---|---|---|
| `front` | Front Plane | +Z | +X | +Y | 沿 +Z |
| `top` | Top Plane | +Y | +X | −Z | 沿 +Y（高度） |
| `right` | Right Plane | +X | −Z | +Y | 沿 +X |

轮廓点 `(a, b)` 的世界坐标 = `planeOrigin + u*a + v*b`，再沿 ±n 拉伸 `depth`。

注意 `top` 平面的 v 轴朝 −Z：写 `center:[0, 31]` 得到 Z = −31（靠观察者一侧）。

## joints[]

```json
{
  "id": "hinge", "type": "revolute",
  "a": "base", "b": "arm",
  "anchor": [0, "$plate_t", 0],
  "axis": [1, 0, 0],
  "range": [12, 110],
  "rest": "$open_angle"
}
```

| 字段 | 说明 |
|---|---|
| `type` | 目前只支持 `revolute`（转动副） |
| `a` / `b` | 父件 / 子件；子件绕 `anchor` 沿 `axis` 旋转 `rest` 度作为初始姿态 |
| `anchor` | 转轴经过的点（世界坐标，mm） |
| `axis` | 转轴方向（单位向量，非归一化也可） |
| `range` | 允许角度区间（度），用于扫掠与干涉检查 |
| `rest` | 初始/静止角度（度） |

限制：**一层关节**。嵌套（臂上再挂一段）当前不支持，需要时拆成两个版本分别验证。

## checks

```json
{ "minWall": 1.6, "minCorner": 1.0, "minClearance": 0.4 }
```

- `minWall`：允许的最小壁厚，低于则告警
- `minCorner`：最小内圆角建议（仅提示）
- `minClearance`：运动件之间的最小间隙

## 表达式规则

- 允许：数字、`+ - * / ( )`、空格、`$param`
- 禁止：函数、变量、对比、字符串
- 求值在浏览器与 python 里都做了白名单校验（`eval` 前正则过滤），非法表达式直接报错

```json
"depth": "$plate_t + 0.3"
"w": "$plate_w - 18"
"center": [0, "$arm_h/2 + 8"]
```

> 惯例：切除平面的 offset 用「被切表面 + 0.3」，避免零厚度；配合 `dir:"down"` 保证切透但不切歪。

## 校验清单（agent 写完后自查）

1. 每个 feature 的 `part` 都在 `parts` 里
2. 所有 `$xxx` 都在 `params` 里，且没有循环引用
3. `cut` 特征的 `offset` 比目标表面多 0.2–0.3mm
4. `joints.b` 的零件几何确实跨过 `anchor`（否则转起来是分离的）
5. `range` 与 `rest` 相容：`rest ∈ [range.min, range.max]`
6. `checks.minWall` 与 `meta.process` 匹配（见 06-dfm-and-qa.md）

## 可扩展字段（不影响现有工具）

`meta.assumptions`（字符串数组）、`features[].note`（工艺说明）、`joints[].note`。
预览器与构建器会忽略未知字段，可以放心加注释。
