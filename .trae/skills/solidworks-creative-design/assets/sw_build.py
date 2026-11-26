#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sw_build.py — 由 spec.json 驱动 SolidWorks 参数化建模

用法:
  python sw_build.py spec.json --plan            # 不连 SW，只打印特征计划（任意平台可跑）
  python sw_build.py spec.json --check           # 只做 spec 校验
  python sw_build.py spec.json --out ./build     # Windows + SolidWorks，真正建模
  python sw_build.py spec.json --out ./build --drawing   # 附带三视图工程图（实验性）
  python sw_build.py spec.json --out ./build --visible   # 显示 SW 界面

依赖: Windows + SolidWorks(2018+) + pywin32  (pip install pywin32)

约定: spec 坐标 = SW 模型坐标 (X 右 / Y 上 / Z 朝观察者)，单位 mm，API 内部统一换算为 m。
"""
import argparse
import json
import math
import os
import re
import sys
import traceback
from datetime import datetime

# ---------------------------------------------------------------- 坐标系帧
# 与 viewer.html 完全一致，保证预览与 SW 模型同构
FRAMES = {
    "front": {"sw": "Front Plane", "n": (0, 0, 1), "u": (1, 0, 0), "v": (0, 1, 0)},
    "top":   {"sw": "Top Plane",   "n": (0, 1, 0), "u": (1, 0, 0), "v": (0, 0, -1)},
    "right": {"sw": "Right Plane", "n": (1, 0, 0), "u": (0, 0, -1), "v": (0, 1, 0)},
}

NUM_RE = re.compile(r"^[0-9+\-*/().\s]+$")


# ---------------------------------------------------------------- 表达式求值
def eval_num(v, params, ctx=""):
    """安全求值: 支持数字 / '$param' / 'expr'，仅允许四则运算与括号。"""
    if isinstance(v, (int, float)):
        return float(v)
    if v is None or v == "":
        return 0.0
    s = str(v).strip()
    if re.match(r"^-?\d+(\.\d+)?$", s):
        return float(s)

    def repl(m):
        k = m.group(1)
        if k not in params:
            raise KeyError(f"未知参数 ${k}" + (f"（{ctx}）" if ctx else ""))
        return f"({params[k]})"

    expr = re.sub(r"\$([A-Za-z_]\w*)", repl, s)
    if not NUM_RE.match(expr):
        raise ValueError(f"非法表达式: {s}" + (f"（{ctx}）" if ctx else ""))
    val = eval(expr, {"__builtins__": {}}, {})  # 已白名单校验，仅算术
    if not math.isfinite(val):
        raise ValueError(f"非有限值: {s}")
    return float(val)


def resolve_params(spec):
    p = {}
    for item in spec.get("params", []):
        p[item["key"]] = float(item.get("value", 0))
    return p


# ---------------------------------------------------------------- 计划与校验
def validate(spec):
    errors, warns = [], []
    if not spec.get("features"):
        errors.append("features 为空")
    ids = set()
    part_ids = {p["id"] for p in spec.get("parts", [])} or {"base"}
    for f in spec.get("features", []):
        fid = f.get("id")
        if not fid:
            errors.append("存在缺少 id 的特征")
            continue
        if fid in ids:
            errors.append(f"特征 id 重复: {fid}")
        ids.add(fid)
        sk = f.get("sketch") or {}
        if sk.get("plane") not in FRAMES:
            errors.append(f"{fid}: sketch.plane 必须是 front/top/right")
        prof = sk.get("profile") or {}
        if prof.get("kind", "rect") not in ("rect", "circle", "polygon"):
            errors.append(f"{fid}: profile.kind 必须是 rect/circle/polygon")
        if f.get("part") and f["part"] not in part_ids:
            warns.append(f"{fid}: part '{f['part']}' 未在 parts 中声明")
        if not f.get("extrude"):
            errors.append(f"{fid}: 缺少 extrude")
    for j in spec.get("joints", []):
        for side in ("a", "b"):
            if j.get(side) not in part_ids:
                warns.append(f"关节 {j.get('id')}: 零件 '{j.get(side)}' 未声明")
    return errors, warns


def plan(spec):
    """展开为可读的特征树（也用于 --plan / --check 输出）"""
    params = resolve_params(spec)
    steps = []
    idx = 1
    for f in spec.get("features", []):
        fid = f.get("id", "?")
        sk = f["sketch"]
        frame = FRAMES[sk["plane"]]
        off = eval_num(sk.get("offset", 0), params, fid)
        prof = sk["profile"]
        depth = eval_num(f["extrude"].get("depth", 0), params, fid)
        direction = f["extrude"].get("dir", "up")
        draft = eval_num(f["extrude"].get("draft", 0), params, fid)
        op = f.get("op", "add")
        kind = prof.get("kind", "rect")

        if kind == "rect":
            desc = (f"矩形 {eval_num(prof.get('w'), params, fid):.2f}×"
                    f"{eval_num(prof.get('h'), params, fid):.2f}"
                    f" R{eval_num(prof.get('cr', 0), params, fid):.2f}")
        elif kind == "circle":
            d = eval_num(prof.get("d", 0), params, fid) or eval_num(prof.get("r", 0), params, fid) * 2
            desc = f"圆 Ø{d:.2f}"
        else:
            desc = f"多边形 {len(prof.get('pts', []))} 点"

        steps.append({
            "n": idx,
            "id": fid,
            "label": f.get("label", ""),
            "op": op,
            "plane": f"{frame['sw']} @ {off:+.2f}",
            "profile": desc,
            "depth": depth,
            "dir": direction,
            "draft": draft,
        })
        idx += 1
    return steps


def print_plan(spec, steps):
    print(f"\n■ {spec.get('meta', {}).get('name', '未命名')}  ({spec.get('meta', {}).get('revision', '-')})")
    print(f"  材料 {spec.get('meta', {}).get('material', '-')} / 工艺 {spec.get('meta', {}).get('process', '-')}")
    print("  " + "-" * 76)
    for s in steps:
        tag = "切除" if s["op"] == "cut" else "凸台"
        dr = f" 拔模{s['draft']:.1f}°" if s["draft"] else ""
        print(f"  {s['n']:>2}. [{tag}] {s['id']:<16} {s['plane']:<26} {s['profile']:<28} "
              f"深{s['depth']:.2f}{dr} {'↓' if s['dir'] == 'down' else '↑'}")
    print("  " + "-" * 76)
    print(f"  共 {len(steps)} 个特征，{len(spec.get('joints', []))} 个关节\n")


# ---------------------------------------------------------------- SW 执行器
class SolidWorks:
    SW_UNITS_PART_TEMPLATE = 8

    def __init__(self, visible=True):
        import win32com.client as win32  # 延迟导入，非 Windows 平台也能 --plan
        self.app = win32.Dispatch("SldWorks.Application")
        self.app.Visible = bool(visible)
        self.model = None

    # -- 文档 ---------------------------------------------------------
    def new_part(self):
        tpl = ""
        try:
            tpl = self.app.GetUserPreferenceStringValue(self.SW_UNITS_PART_TEMPLATE) or ""
        except Exception:
            pass
        m = self.app.NewDocument(tpl, 0, 0, 0)
        if m is None:
            raise RuntimeError("无法新建零件（请检查 SW 默认零件模板：工具>选项>文件位置）")
        self.model = m
        try:
            self.model.Extension.SetUserPreferenceInteger(49, 0, 0)  # swUnitsLinear = MM
        except Exception:
            pass
        return m

    def ref_plane(self, base_name, offset_mm):
        """在 base 平面法向偏移 offset_mm 处建参考平面，返回平面名。"""
        if abs(offset_mm) < 1e-6:
            return base_name
        model = self.model
        model.ClearSelection2(True)
        sel = model.Extension.SelectByID2(base_name, "PLANE", 0, 0, 0, False, 0, None, 0)
        if not sel:
            raise RuntimeError(f"选不中基准面 {base_name}")
        try:
            feat = model.FeatureManager.InsertRefPlane(8, offset_mm / 1000.0, 0, 0, 0, 0)
        except Exception:
            feat = None
        if feat is None:
            # 负偏移在部分版本会被拒，翻到反向再试
            model.ClearSelection2(True)
            model.Extension.SelectByID2(base_name, "PLANE", 0, 0, 0, False, 0, None, 0)
            feat = model.FeatureManager.InsertRefPlane(8, -offset_mm / 1000.0, 0, 0, 0, 0)
            if feat is None:
                raise RuntimeError(f"无法创建偏移参考平面（{base_name} {offset_mm}mm）")
            print(f"    ! 偏移反向补偿: {base_name} {offset_mm} -> {-offset_mm}")
        return feat.Name

    # -- 草图 ---------------------------------------------------------
    def sketch_profile(self, plane_name, profile, params, fid):
        model = self.model
        sm = model.SketchManager
        model.ClearSelection2(True)
        if not model.Extension.SelectByID2(plane_name, "PLANE", 0, 0, 0, False, 0, None, 0):
            raise RuntimeError(f"选不中草图平面 {plane_name}")
        sm.InsertSketch(True)
        scale = 0.001  # mm -> m

        kind = profile.get("kind", "rect")
        if kind == "circle":
            c = profile.get("center", [0, 0])
            cx = eval_num(c[0], params, fid) * scale
            cy = eval_num(c[1], params, fid) * scale
            r = (eval_num(profile.get("d"), params, fid) / 2.0 if profile.get("d") is not None
                 else eval_num(profile.get("r"), params, fid)) * scale
            sm.CreateCircleByRadius(cx, cy, 0, r)
        elif kind == "polygon":
            pts = [(eval_num(p[0], params, fid) * scale, eval_num(p[1], params, fid) * scale)
                   for p in profile.get("pts", [])]
            for i in range(len(pts)):
                x1, y1 = pts[i]
                x2, y2 = pts[(i + 1) % len(pts)]
                sm.CreateLine(x1, y1, 0, x2, y2, 0)
        else:
            w = eval_num(profile.get("w"), params, fid) * scale
            h = eval_num(profile.get("h"), params, fid) * scale
            c = profile.get("center", [0, 0])
            cx = eval_num(c[0], params, fid) * scale
            cy = eval_num(c[1], params, fid) * scale
            cr = min(eval_num(profile.get("cr", 0), params, fid) * scale, min(w, h) / 2.0 - 1e-5)
            x0, x1 = cx - w / 2, cx + w / 2
            y0, y1 = cy - h / 2, cy + h / 2
            if cr <= 1e-6:
                sm.CreateLine(x0, y0, 0, x1, y0, 0)
                sm.CreateLine(x1, y0, 0, x1, y1, 0)
                sm.CreateLine(x1, y1, 0, x0, y1, 0)
                sm.CreateLine(x0, y1, 0, x0, y0, 0)
            else:
                sm.CreateLine(x0 + cr, y0, 0, x1 - cr, y0, 0)
                sm.CreateArc(x1 - cr, y0 + cr, 0, x1 - cr, y0, 0, x1, y0 + cr, 0, 1)
                sm.CreateLine(x1, y0 + cr, 0, x1, y1 - cr, 0)
                sm.CreateArc(x1 - cr, y1 - cr, 0, x1, y1 - cr, 0, x1 - cr, y1, 0, 1)
                sm.CreateLine(x1 - cr, y1, 0, x0 + cr, y1, 0)
                sm.CreateArc(x0 + cr, y1 - cr, 0, x0 + cr, y1, 0, x0, y1 - cr, 0, 1)
                sm.CreateLine(x0, y1 - cr, 0, x0, y0 + cr, 0)
                sm.CreateArc(x0 + cr, y0 + cr, 0, x0, y0 + cr, 0, x0 + cr, y0, 0, 1)
        sm.InsertSketch(True)  # 退出草图

    # -- 特征 ---------------------------------------------------------
    def extrude(self, depth_mm, reverse, draft_deg, cut):
        model = self.model
        fm = model.FeatureManager
        d = depth_mm / 1000.0
        draft = math.radians(abs(draft_deg)) if draft_deg else 0.0
        has_draft = 1 if draft_deg else 0
        args = (True, False, bool(reverse), 0, 0, d, d,
                has_draft, 0, draft, draft,
                False, False, False, False,
                True, True, True, 0, 0, False)
        feat = None
        if cut:
            for name in ("FeatureCut4", "FeatureCut3", "FeatureCut2"):
                fn = getattr(fm, name, None)
                if fn is None:
                    continue
                try:
                    a = args if name != "FeatureCut2" else args[:16]
                    feat = fn(*a)
                    if feat:
                        break
                except Exception:
                    feat = None
        else:
            for name in ("FeatureExtrusion3", "FeatureExtrusion2"):
                fn = getattr(fm, name, None)
                if fn is None:
                    continue
                try:
                    a = args if name == "FeatureExtrusion3" else args[:18]
                    feat = fn(*a)
                    if feat:
                        break
                except Exception:
                    feat = None
        model.ClearSelection2(True)
        if feat is None:
            raise RuntimeError("拉伸特征创建失败（可能是 SW 版本 API 差异，见 references/solidworks-api.md）")
        return feat

    def fillet_all(self, radius_mm):
        """对当前零件的所有边做圆角（版本相关，失败则跳过）。"""
        if radius_mm <= 0:
            return None
        model = self.model
        fm = model.FeatureManager
        r = radius_mm / 1000.0
        for sig in ("FeatureFillet3", "FeatureFillet2", "FeatureFillet"):
            fn = getattr(fm, sig, None)
            if fn is None:
                continue
            try:
                feat = fn(r, 0, 0, 0, 0, 0)
                if feat:
                    return feat
            except Exception:
                pass
        print(f"    ! 圆角 R{radius_mm} 跳过（API 不可用），可在 SW 中手动补")
        return None

    def mass_props(self):
        try:
            mp = self.model.Extension.GetMassProperties2(1, None, False)
            return {"raw": list(mp) if mp else None}
        except Exception:
            return {"raw": None}

    def save(self, path_sldprt):
        os.makedirs(os.path.dirname(path_sldprt) or ".", exist_ok=True)
        ok = self.model.SaveAs3(path_sldprt, 0, 0)
        if ok is False:
            raise RuntimeError(f"保存失败: {path_sldprt}")
        return path_sldprt

    def export(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        try:
            # SaveAs3 对 .step/.stl 同样生效（按扩展名自动选择导出器）
            self.model.SaveAs3(path, 0, 0)
            return path
        except Exception as e:
            print(f"    ! 导出失败 {path}: {e}")
            return None

    def drawing(self, model_path, out_path):
        """生成三视图工程图（实验性，模板缺失会失败）。"""
        try:
            tpl = self.app.GetUserPreferenceStringValue(10) or ""
            drw = self.app.NewDocument(tpl, 0, 0, 0)
            if drw is None:
                raise RuntimeError("无法新建工程图（缺默认模板）")
            m = 0.001
            drw.CreateDrawViewFromModelView3(model_path, "*Front", 0.10, 0.14, 0)
            drw.CreateDrawViewFromModelView3(model_path, "*Top", 0.10, 0.05, 0)
            drw.CreateDrawViewFromModelView3(model_path, "*Right", 0.28, 0.14, 0)
            try:
                drw.InsertModelAnnotations3(0, 1 | 2 | 4 | 8, True, True, True, False)
            except Exception:
                pass
            drw.SaveAs3(out_path, 0, 0)
            return out_path
        except Exception as e:
            print(f"    ! 工程图生成失败（不影响模型）: {e}")
            return None


# ---------------------------------------------------------------- 主流程
def build(spec, out_dir, visible, drawing, dry_run):
    params = resolve_params(spec)
    steps = plan(spec)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = re.sub(r"[^\w\-]+", "_", spec.get("meta", {}).get("name", "part"))
    base = os.path.join(out_dir, f"{name}_{stamp}")

    if dry_run:
        print("[dry-run] 未连接 SolidWorks。")
        return {"ok": True, "dry_run": True, "steps": len(steps)}

    sw = SolidWorks(visible=visible)
    sw.new_part()
    report = {"ok": True, "created": [], "failed": [], "auto_fillet": []}

    for step, feat_def in zip(steps, spec["features"]):
        fid = step["id"]
        try:
            frame = FRAMES[feat_def["sketch"]["plane"]]
            off = eval_num(feat_def["sketch"].get("offset", 0), params, fid)
            plane = sw.ref_plane(frame["sw"], off)
            sw.sketch_profile(plane, feat_def["sketch"]["profile"], params, fid)
            depth = eval_num(feat_def["extrude"].get("depth", 0), params, fid)
            draft = eval_num(feat_def["extrude"].get("draft", 0), params, fid)
            cut = feat_def.get("op", "add") == "cut"
            sw.extrude(depth, step["dir"] == "down", draft, cut)

            # 草图自带圆角半径时，作为自动圆角建议
            cr = feat_def["sketch"]["profile"].get("cr")
            if not cut and cr and not feat_def.get("fillet"):
                report["auto_fillet"].append({"id": fid, "radius": eval_num(cr, params, fid)})
            report["created"].append(fid)
            print(f"  ✓ {fid}")
        except Exception as e:
            report["failed"].append({"id": fid, "error": str(e)})
            report["ok"] = False
            print(f"  ✗ {fid}: {e}")
            traceback.print_exc(limit=1)

    # 整零件圆角（spec 顶层 fillet 或自动）
    top_fillet = eval_num(spec.get("fillet", 0), params)
    if top_fillet > 0:
        sw.fillet_all(top_fillet)

    part_path = base + ".SLDPRT"
    sw.save(part_path)
    report["part"] = part_path
    report["step"] = sw.export(base + ".step")
    report["stl"] = sw.export(base + ".stl")
    report["mass"] = sw.mass_props()
    if drawing:
        report["drawing"] = sw.drawing(part_path, base + ".SLDDRW")

    rp = base + "_report.json"
    with open(rp, "w", encoding="utf-8") as f:
        json.dump({**report, "spec_name": spec.get("meta", {}).get("name"),
                   "generated": datetime.now().isoformat(timespec="seconds")}, f, ensure_ascii=False, indent=2)
    print(f"\n■ 完成: {part_path}\n  报告: {rp}")
    if report["failed"]:
        print(f"  ! 有 {len(report['failed'])} 个特征失败，见报告。")
    return report


def main():
    ap = argparse.ArgumentParser(description="spec.json → SolidWorks 参数化建模")
    ap.add_argument("spec", help="spec.json 路径")
    ap.add_argument("--out", default="./build", help="输出目录")
    ap.add_argument("--plan", action="store_true", help="只打印特征计划（不连 SW）")
    ap.add_argument("--check", action="store_true", help="只做校验")
    ap.add_argument("--drawing", action="store_true", help="额外生成三视图工程图")
    ap.add_argument("--visible", action="store_true", help="显示 SolidWorks 界面")
    ap.add_argument("--allow-fail", action="store_true", help="有特征失败时仍返回 0")
    args = ap.parse_args()

    with open(args.spec, "r", encoding="utf-8") as f:
        spec = json.load(f)

    errors, warns = validate(spec)
    for w in warns:
        print(f"[warn] {w}")
    if errors:
        for e in errors:
            print(f"[error] {e}")
        sys.exit(2)

    try:
        steps = plan(spec)
    except Exception as e:
        print(f"[error] 计划展开失败: {e}")
        sys.exit(2)

    print_plan(spec, steps)
    if args.check:
        print("[check] spec 校验通过。")
        return
    if args.plan:
        return

    try:
        rep = build(spec, args.out, args.visible, args.drawing, dry_run=False)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    if not rep.get("ok") and not args.allow_fail:
        sys.exit(3)


if __name__ == "__main__":
    main()
