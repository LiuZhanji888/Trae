/**
 * spec-core.js — spec.json 的纯几何内核（不依赖 DOM）
 * 坐标系与 SolidWorks 模型坐标一致：X 右 / Y 上 / Z 朝观察者，单位 mm。
 * viewer.html 与测试脚本共用此文件；sw_build.py 遵循同一套规则。
 */

export const FRAMES = {
  front: { n: [0, 0, 1], u: [1, 0, 0], v: [0, 1, 0], origin: (o) => [0, 0, o] },
  top:   { n: [0, 1, 0], u: [1, 0, 0], v: [0, 0, -1], origin: (o) => [0, o, 0] },
  right: { n: [1, 0, 0], u: [0, 0, -1], v: [0, 1, 0], origin: (o) => [o, 0, 0] },
};

const NUM_RE = /^[0-9+\-*/().\s]+$/;

/** 安全表达式求值：支持数字、`$param`、四则运算与括号 */
export function evalNum(v, params) {
  if (typeof v === 'number') return v;
  if (v === undefined || v === null || v === '') return 0;
  const s = String(v).trim();
  if (/^-?\d+(\.\d+)?$/.test(s)) return parseFloat(s);
  const expr = s.replace(/\$([A-Za-z_]\w*)/g, (_, k) => {
    if (!(k in params)) throw new Error('未知参数 $' + k);
    return '(' + params[k] + ')';
  });
  if (!NUM_RE.test(expr)) throw new Error('非法表达式：' + s);
  const val = Function('"use strict";return (' + expr + ');')();
  if (!isFinite(val)) throw new Error('表达式非有限值：' + s);
  return val;
}

/** 2D 轮廓 → THREE.Shape（位于草图平面局部坐标 u-v） */
export function buildShape(THREE, profile, params) {
  const P = (x) => evalNum(x, params);
  const kind = profile.kind || 'rect';
  const shape = new THREE.Shape();

  if (kind === 'circle') {
    const c = profile.center || [0, 0];
    const rr = profile.r !== undefined ? P(profile.r) : P(profile.d) / 2;
    shape.absarc(P(c[0]), P(c[1]), Math.max(rr, 0.05), 0, Math.PI * 2, false);
    return shape;
  }

  if (kind === 'polygon') {
    (profile.pts || []).forEach((p, i) => {
      const x = P(p[0]), y = P(p[1]);
      i ? shape.lineTo(x, y) : shape.moveTo(x, y);
    });
    shape.closePath();
    return shape;
  }

  const w = Math.max(P(profile.w), 0.1);
  const h = Math.max(P(profile.h), 0.1);
  const c = profile.center || [0, 0];
  const cx = P(c[0]), cy = P(c[1]);
  const cr = Math.max(Math.min(P(profile.cr || 0), Math.min(w, h) / 2), 0);
  const x0 = cx - w / 2, x1 = cx + w / 2, y0 = cy - h / 2, y1 = cy + h / 2;

  if (cr <= 0.01) {
    shape.moveTo(x0, y0);
    shape.lineTo(x1, y0);
    shape.lineTo(x1, y1);
    shape.lineTo(x0, y1);
    shape.closePath();
    return shape;
  }
  shape.moveTo(x0 + cr, y0);
  shape.lineTo(x1 - cr, y0); shape.quadraticCurveTo(x1, y0, x1, y0 + cr);
  shape.lineTo(x1, y1 - cr); shape.quadraticCurveTo(x1, y1, x1 - cr, y1);
  shape.lineTo(x0 + cr, y1); shape.quadraticCurveTo(x0, y1, x0, y1 - cr);
  shape.lineTo(x0, y0 + cr); shape.quadraticCurveTo(x0, y0, x0 + cr, y0);
  shape.closePath();
  return shape;
}

/** 轮廓面积（mm²），用于体积/质量估算 */
export function profileArea(profile, params) {
  const P = (x) => evalNum(x, params);
  const kind = profile.kind || 'rect';
  if (kind === 'circle') {
    const rr = profile.r !== undefined ? P(profile.r) : P(profile.d) / 2;
    return Math.PI * rr * rr;
  }
  if (kind === 'polygon') {
    const pts = profile.pts || [];
    let a = 0;
    for (let i = 0; i < pts.length; i++) {
      const [x1, y1] = pts[i], [x2, y2] = pts[(i + 1) % pts.length];
      a += P(x1) * P(y2) - P(x2) * P(y1);
    }
    return Math.abs(a) / 2;
  }
  return Math.max(P(profile.w), 0) * Math.max(P(profile.h), 0);
}

/**
 * 构建全部特征的几何体（不做材质/场景）
 * @returns {{ geoms:Array, parts:Object, joints:Array, errors:Array }}
 */
export function buildModel(THREE, spec, params) {
  const geoms = [], errors = [];
  const parts = {};
  (spec.parts || []).forEach((p) => {
    parts[p.id] = { id: p.id, label: p.label || p.id, volume: 0, features: [] };
  });
  if (!Object.keys(parts).length) parts.base = { id: 'base', label: 'base', volume: 0, features: [] };

  (spec.features || []).forEach((def, idx) => {
    const fid = def.id || '#' + idx;
    try {
      const frame = FRAMES[def.sketch?.plane || 'top'];
      if (!frame) throw new Error('未知平面 ' + def.sketch?.plane);
      const shape = buildShape(THREE, def.sketch.profile, params);
      const depth = Math.max(evalNum(def.extrude.depth, params), 0.01);
      const dir = (def.extrude.dir || 'up') === 'down' ? -1 : 1;
      const isCut = (def.op || 'add') === 'cut';

      const d = isCut ? depth + 0.6 : depth;
      const geo = new THREE.ExtrudeGeometry(shape, { depth: d, bevelEnabled: false, curveSegments: 32 });
      if (dir < 0) geo.translate(0, 0, -d);
      if (isCut) geo.translate(0, 0, -0.28);

      const basis = new THREE.Matrix4().makeBasis(
        new THREE.Vector3(...frame.u),
        new THREE.Vector3(...frame.v),
        new THREE.Vector3(...frame.n)
      );
      const o = frame.origin(evalNum(def.sketch.offset || 0, params));
      basis.setPosition(o[0], o[1], o[2]);
      geo.applyMatrix4(basis);
      geo.computeVertexNormals();
      geo.computeBoundingBox();

      const volume = profileArea(def.sketch.profile, params) * depth;
      const rec = {
        id: fid, label: def.label || fid, def, geometry: geo,
        bbox: geo.boundingBox.clone(), volume, op: def.op || 'add',
        part: def.part || Object.keys(parts)[0],
      };
      geoms.push(rec);
      const part = parts[rec.part];
      if (part) {
        part.features.push(rec);
        part.volume += (isCut ? -1 : 1) * volume;
      }
    } catch (e) {
      errors.push({ id: fid, message: e.message });
    }
  });

  const joints = (spec.joints || []).map((j) => {
    const a = j.anchor || [0, 0, 0];
    return {
      def: j,
      a: j.a, b: j.b,
      anchor: [evalNum(a[0], params), evalNum(a[1], params), evalNum(a[2], params)],
      axis: (j.axis || [1, 0, 0]).map(Number),
      rest: evalNum(j.rest ?? j.range?.[0] ?? 0, params),
      range: (j.range || [-180, 180]).map((v) => evalNum(v, params)),
      type: j.type || 'revolute',
    };
  });

  return { geoms, parts, joints, errors };
}

/** 由模型汇总指标：体积、质量、重心、包围盒、近似最小壁厚 */
export function modelMetrics(THREE, model, spec) {
  const density = Number(spec.meta?.density || 1.24);
  const adds = model.geoms.filter((g) => g.op !== 'cut');
  const cuts = model.geoms.filter((g) => g.op === 'cut');

  let bbox = null, volume = 0, cm = new THREE.Vector3(), wsum = 0;
  adds.forEach((g) => {
    bbox = bbox ? bbox.union(g.bbox) : g.bbox.clone();
    volume += g.volume;
    cm.addScaledVector(g.bbox.getCenter(new THREE.Vector3()), g.volume);
    wsum += g.volume;
  });
  cuts.forEach((g) => { volume -= g.volume; });
  volume = Math.max(volume, 0);
  if (wsum > 0) cm.divideScalar(wsum);
  if (!bbox) bbox = new THREE.Box3();

  const size = bbox.getSize(new THREE.Vector3());
  const mass = volume * density / 1000; // mm³ → cm³ → g

  let minWall = Infinity;
  Object.values(model.parts).forEach((p) => {
    const pa = p.features.filter((f) => f.op !== 'cut');
    const pc = p.features.filter((f) => f.op === 'cut');
    if (!pa.length || !pc.length) return;
    const outer = pa.reduce((acc, f) => (acc ? acc.union(f.bbox) : f.bbox.clone()), null);
    pc.forEach((f) => {
      const b = f.bbox;
      // 只统计“被外轮廓包住”的侧面；穿出表面的方向视为开口，不计壁厚
      const gaps = [
        b.min.x - outer.min.x, outer.max.x - b.max.x,
        b.min.z - outer.min.z, outer.max.z - b.max.z,
      ].filter((d) => d > 0.05);
      if (gaps.length) minWall = Math.min(minWall, Math.min(...gaps));
    });
  });

  return {
    volume, mass, cm, size, bbox, density,
    minWall: isFinite(minWall) ? minWall : null,
  };
}
