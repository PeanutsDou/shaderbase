// shaderbase 知识图谱 — 3D 星系可视化
// 1:1 复刻 codebase-memory graph-ui 的渲染逻辑（vanilla JS + three.js）
//
// 模块对齐参考：
//   lib/density.ts         → §1 density compensation
//   lib/colors.ts          → §2 colors (stellar + label + status)
//   lib/types.ts           → §3 GraphNode/GraphEdge/GraphData
//   GraphScene.tsx         → §4 scene + camera animator + idle rotate + bloom
//   NodeCloud.tsx          → §5 instanced spheres + point-sprite fallback
//   EdgeLines.tsx          → §6 additive-blended LineSegments
//   NodeLabels.tsx         → §7 canvas-texture sprite labels
//   NodeTooltip.tsx       → §8 hover tooltip
//   NodeDetailPanel.tsx    → §9 right detail panel
//   Sidebar.tsx            → §10 file tree
//   FilterPanel.tsx        → §11 filter panel
//   GraphTab.tsx           → §12 graph controller (filter+rebuild+budget)
//   useGraphData.ts        → §13 fetch layout
//   DisplaySettingsMenu.tsx→ §14 display sliders

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/OrbitControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';

// ════════════════════════════════════════════════════════
// §1 密度补偿（1:1 照搬 density.ts）
// ════════════════════════════════════════════════════════

const EDGE_REFERENCE_COUNT = 2500;
const EDGE_MIN_SCALE = 0.05;
const NODE_REFERENCE_COUNT = 25000;
const NODE_FADE_END = 250000;
const BLOOM_FLOOR = 0.7;
const NODE_BOOST_FLOOR = 0.8;

function edgeIntensityScale(edgeCount) {
  if (edgeCount <= EDGE_REFERENCE_COUNT) return 1;
  return Math.max(EDGE_MIN_SCALE, Math.sqrt(EDGE_REFERENCE_COUNT / edgeCount));
}

function fadeFactor(nodeCount) {
  if (nodeCount <= NODE_REFERENCE_COUNT) return 0;
  return Math.min(1, (nodeCount - NODE_REFERENCE_COUNT) / (NODE_FADE_END - NODE_REFERENCE_COUNT));
}

function bloomIntensityScale(nodeCount) {
  return 1 - fadeFactor(nodeCount) * (1 - BLOOM_FLOOR);
}

function nodeBoostScale(nodeCount) {
  return 1 - fadeFactor(nodeCount) * (1 - NODE_BOOST_FLOOR);
}

// 色彩感知的辉光倍率（1:1 照搬 nodeGlowBoost）
const GLOW_BASE = 1.35;
const GLOW_BLUE_GAIN = 2.4;
const GLOW_RED_GAIN = 0.9;

function nodeGlowBoost(r, g, b) {
  const blueness = Math.max(0, b - Math.max(r, g));
  const redness = Math.max(0, r - Math.max(g, b));
  return GLOW_BASE + blueness * GLOW_BLUE_GAIN + redness * GLOW_RED_GAIN;
}

// ════════════════════════════════════════════════════════
// §2 颜色映射（1:1 照搬 colors.ts）
// ════════════════════════════════════════════════════════

const LABEL_COLORS = {
  Function: '#06b6d4',
  Method: '#06b6d4',
  Struct: '#22c55e',
  Class: '#a855f7',
  Interface: '#a855f7',
  Uniform: '#f97316',
  Texture: '#3b82f6',
  SamplerState: '#e11d48',
  Technique: '#a855f7',
  CBuffer: '#eab308',
  File: '#3b82f6',
  Folder: '#22c55e',
};

const DEFAULT_LABEL_COLOR = '#94a3b8';
function colorForLabel(label) { return LABEL_COLORS[label] || DEFAULT_LABEL_COLOR; }

const STATUS_COLORS = {
  dead: '#ef4444',
  single: '#f97316',
  entry: '#3b82f6',
  test: '#a855f7',
  normal: '#22c55e',
  exported: '#475569',
  structural: '#334155',
};
const STATUS_DEFAULT = '#334155';
function colorForStatus(status) { return status ? (STATUS_COLORS[status] || STATUS_DEFAULT) : STATUS_DEFAULT; }

const STATUS_LEGEND = [
  { status: 'dead', label: 'Dead (0 callers)', color: STATUS_COLORS.dead },
  { status: 'single', label: 'One caller', color: STATUS_COLORS.single },
  { status: 'entry', label: 'Entry / route', color: STATUS_COLORS.entry },
  { status: 'test', label: 'Test', color: STATUS_COLORS.test },
  { status: 'normal', label: 'Normal', color: STATUS_COLORS.normal },
];

const STELLAR_LEGEND = [
  { type: 'O (Blue Giant)', color: '#80a0ff', description: '50+ connections' },
  { type: 'B (Blue-White)', color: '#c0d0ff', description: '26-50 connections' },
  { type: 'A (White)', color: '#e8e8ff', description: '13-25 connections' },
  { type: 'F (Yellow-White)', color: '#fff0c0', description: '7-12 connections' },
  { type: 'G (Yellow/Sun)', color: '#ffe080', description: '4-6 connections' },
  { type: 'K (Orange)', color: '#ffa060', description: '2-3 connections' },
  { type: 'M (Red Dwarf)', color: '#ff6050', description: '0-1 connections' },
];

// 边类型 → 颜色（1:1 照搬 EdgeLines EDGE_TYPE_COLORS + shader 扩展）
const EDGE_TYPE_COLORS = {
  CALLS: '#1DA27E',
  INCLUDES: '#3b82f6',
  HAS_MEMBER: '#22c55e',
  IS_ENTRY_POINT: '#a855f7',
  EXPOSES_TECHNIQUE: '#a855f7',
  IMPORTS: '#3b82f6',
  DEFINES: '#a855f7',
  CONTAINS_FILE: '#22c55e',
  HANDLES: '#eab308',
  IMPLEMENTS: '#f97316',
  HTTP_CALLS: '#e11d48',
  ASYNC_CALLS: '#ec4899',
  MEMBER_OF: '#64748b',
};
const DEFAULT_EDGE_COLOR = '#1C8585';

// ════════════════════════════════════════════════════════
// §3 DisplaySettings（1:1 照搬 density.ts DisplaySettings）
// ════════════════════════════════════════════════════════

const DEFAULT_DISPLAY_SETTINGS = { edgeBrightness: 1, nodeGlow: 1, bloom: 1 };
const DISPLAY_LIMITS = {
  edgeBrightness: { min: 0.1, max: 3 },
  nodeGlow: { min: 0, max: 2 },
  bloom: { min: 0, max: 2 },
};
const DISPLAY_STORAGE_KEY = 'shaderbase-display';

function loadDisplaySettings() {
  try {
    const raw = localStorage.getItem(DISPLAY_STORAGE_KEY);
    if (raw) {
      const v = JSON.parse(raw);
      return {
        edgeBrightness: clampSetting('edgeBrightness', v.edgeBrightness),
        nodeGlow: clampSetting('nodeGlow', v.nodeGlow),
        bloom: clampSetting('bloom', v.bloom),
      };
    }
  } catch (_) {}
  return { ...DEFAULT_DISPLAY_SETTINGS };
}
function clampSetting(key, value) {
  const { min, max } = DISPLAY_LIMITS[key];
  const n = typeof value === 'number' ? value : NaN;
  if (!Number.isFinite(n)) return DEFAULT_DISPLAY_SETTINGS[key];
  return Math.min(max, Math.max(min, n));
}
function saveDisplaySettings(s) {
  try { localStorage.setItem(DISPLAY_STORAGE_KEY, JSON.stringify(s)); } catch (_) {}
}

// ════════════════════════════════════════════════════════
// §3.5 node budget（1:1 照搬 useGraphData.ts）
// ════════════════════════════════════════════════════════

const GRAPH_RENDER_NODE_LIMIT = 5000;
const GRAPH_NODE_BUDGET_STEP = 5000;
const GRAPH_NODE_BUDGET_MAX = 10_000_000;

function clampNodeBudget(value) {
  if (!Number.isFinite(value)) return GRAPH_RENDER_NODE_LIMIT;
  const stepped = Math.round(value / GRAPH_NODE_BUDGET_STEP) * GRAPH_NODE_BUDGET_STEP;
  if (stepped < GRAPH_NODE_BUDGET_STEP) return GRAPH_NODE_BUDGET_STEP;
  if (stepped > GRAPH_NODE_BUDGET_MAX) return GRAPH_NODE_BUDGET_MAX;
  return stepped;
}
function budgetKey(project) { return 'shaderbase-node-budget:' + project; }
function loadNodeBudget(project) {
  try {
    const v = localStorage.getItem(budgetKey(project));
    if (v) return clampNodeBudget(parseInt(v, 10));
  } catch (_) {}
  return GRAPH_RENDER_NODE_LIMIT;
}
function saveNodeBudget(project, value) {
  try { localStorage.setItem(budgetKey(project), String(value)); } catch (_) {}
}

// 面板宽度持久化（照搬 GraphTab loadWidth/saveWidth）
function loadWidth(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    if (v) return Math.max(150, Math.min(600, parseInt(v, 10)));
  } catch (_) {}
  return fallback;
}
function saveWidth(key, value) {
  try { localStorage.setItem(key, String(Math.round(value))); } catch (_) {}
}

// ════════════════════════════════════════════════════════
// §4 全局状态 + Scene（1:1 照搬 GraphScene.tsx）
// ════════════════════════════════════════════════════════

const BASE_BLOOM_INTENSITY = 1.45;
const IDLE_TIMEOUT_MS = 60000;
const GRAPH_CANVAS_DPR = [1, 1.5];
const POINT_MODE_THRESHOLD = 75000;

let scene, camera, renderer, controls, composer, bloomPass;
let nodeMesh = null;        // InstancedMesh 或 Points
let nodeMode = 'spheres';  // 'spheres' | 'points'
let edgeLines = null;
let labelGroup = null;
let labelSprites = [];
let raycaster, pointer;
let currentProject = 'g66';

let graphData = null;       // 原始 /api/layout
let filteredData = null;    // 应用 filter 后
let highlightedIds = null;  // Set<number> | null
let selectedNode = null;
let hoveredNode = null;
let enabledLabels = new Set();
let enabledEdgeTypes = new Set();
let showLabels = true;
let deadCodeView = false;
let showOnlyDead = false;
let hideEntryPoints = false;
let hideTests = false;
let repoInfo = null;
let cameraTarget = null;
let cameraAnimProgress = 1;
let lastInteraction = Date.now();

let display = loadDisplaySettings();
let budget = { project: null, value: GRAPH_RENDER_NODE_LIMIT };
let budgetDraft = String(GRAPH_RENDER_NODE_LIMIT);

let leftWidth = loadWidth('shaderbase-left-w', 260);
let rightWidth = loadWidth('shaderbase-right-w', 300);

// 点模式共享 sprite 纹理（1:1 照搬 NodeCloud getPointSprite）
let pointSprite = null;
function getPointSprite() {
  if (pointSprite) return pointSprite;
  const size = 64;
  const canvas = document.createElement('canvas');
  canvas.width = size; canvas.height = size;
  const ctx = canvas.getContext('2d');
  const g = ctx.createRadialGradient(size/2, size/2, 0, size/2, size/2, size/2);
  g.addColorStop(0, 'rgba(255,255,255,1)');
  g.addColorStop(0.5, 'rgba(255,255,255,0.9)');
  g.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = g; ctx.fillRect(0, 0, size, size);
  pointSprite = new THREE.CanvasTexture(canvas);
  return pointSprite;
}

function initScene() {
  const container = document.getElementById('canvas-container');
  const w = container.clientWidth, h = container.clientHeight;

  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x06090f);

  camera = new THREE.PerspectiveCamera(50, w / h, 0.1, 100000);
  camera.position.set(0, 0, 800);

  renderer = new THREE.WebGLRenderer({
    antialias: false, alpha: false, powerPreference: 'high-performance',
  });
  renderer.setSize(w, h);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, GRAPH_CANVAS_DPR[1]));
  container.appendChild(renderer.domElement);

  // 灯照（1:1 照搬 GraphScene lights）
  scene.add(new THREE.AmbientLight(0xffffff, 0.5));
  const p1 = new THREE.PointLight(0xffffff, 0.6); p1.position.set(500, 500, 500); scene.add(p1);
  const p2 = new THREE.PointLight(0x6040ff, 0.4); p2.position.set(-300, -200, -300); scene.add(p2);

  // OrbitControls（1:1 照搬 GraphScene 参数）
  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.rotateSpeed = 0.5;
  controls.zoomSpeed = 1.5;
  controls.minDistance = 10;
  controls.maxDistance = 50000;
  controls.autoRotateSpeed = 0.4;

  raycaster = new THREE.Raycaster();
  raycaster.params.Points = { threshold: 3 };
  pointer = new THREE.Vector2();

  // Bloom postprocessing（1:1 照搬 GraphScene EffectComposer + Bloom）
  composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  bloomPass = new UnrealBloomPass(
    new THREE.Vector2(w, h),
    BASE_BLOOM_INTENSITY,  // intensity，每帧根据 density 更新
    0.6,                   // radius
    0.3,                   // luminanceThreshold
  );
  bloomPass.luminanceSmoothing = 0.7;
  bloomPass.mipmapBlur = true;
  composer.addPass(bloomPass);

  // 事件
  const el = renderer.domElement;
  el.addEventListener('pointermove', onPointerMove);
  el.addEventListener('click', onPointerClick);
  el.addEventListener('pointerdown', () => {
    lastInteraction = Date.now();
    if (controls) controls.autoRotate = false;
  });
  el.addEventListener('wheel', () => { lastInteraction = Date.now(); }, { passive: true });
  window.addEventListener('resize', onResize);

  animate();
}

// ════════════════════════════════════════════════════════
// §5 节点云（1:1 照搬 NodeCloud.tsx — InstancedMesh 或 Points）
// ════════════════════════════════════════════════════════

function sphereDetail(count) {
  if (count <= 8000) return [32, 24];
  if (count <= 25000) return [16, 12];
  return [10, 7];
}

// 1:1 照搬 NodeCloud.tsx nodeColor
function nodeColor(node, highlightedIds, opacity, boost, tempColor) {
  const hasHighlight = highlightedIds && highlightedIds.size > 0;
  tempColor.set(node.color);
  if (hasHighlight && !highlightedIds.has(node.id)) {
    tempColor.multiplyScalar(0.15);
  } else {
    const fullBoost = nodeGlowBoost(tempColor.r, tempColor.g, tempColor.b);
    const applied = 1 + (fullBoost - 1) * boost;
    tempColor.multiplyScalar(applied);
  }
  return [tempColor.r * opacity, tempColor.g * opacity, tempColor.b * opacity];
}

function buildNodeCloud(nodes) {
  // 清旧
  if (nodeMesh) {
    scene.remove(nodeMesh);
    if (nodeMesh.geometry) nodeMesh.geometry.dispose();
    if (nodeMesh.material) nodeMesh.material.dispose();
    nodeMesh = null;
  }

  if (nodes.length === 0) return;

  const count = nodes.length;
  const nodeBoost = nodeBoostScale(count) * display.nodeGlow;
  const hasHighlight = highlightedIds && highlightedIds.size > 0;
  const tempColor = new THREE.Color();

  if (count > POINT_MODE_THRESHOLD) {
    nodeMode = 'points';
    buildNodePoints(nodes, hasHighlight, nodeBoost, tempColor);
  } else {
    nodeMode = 'spheres';
    buildNodeSpheres(nodes, hasHighlight, nodeBoost, tempColor);
  }
}

function buildNodeSpheres(nodes, hasHighlight, nodeBoost, tempColor) {
  const count = nodes.length;
  const [ws, hs] = sphereDetail(count);
  const geo = new THREE.SphereGeometry(1, ws, hs);
  const mat = new THREE.MeshBasicMaterial({ toneMapped: false });
  const mesh = new THREE.InstancedMesh(geo, mat, count);
  mesh.frustumCulled = false;

  // 1:1 照搬 NodeSpheres：先建 instanceColor attribute
  const colorArr = new Float32Array(count * 3);
  const dummy = new THREE.Object3D();
  for (let i = 0; i < count; i++) {
    const n = nodes[i];
    dummy.position.set(n.x, n.y, n.z);
    const isHL = !hasHighlight || highlightedIds.has(n.id);
    // 照搬 NodeSpheres scale：高亮 0.5，非高亮 0.2
    const s = n.size * (isHL ? 0.5 : 0.2);
    dummy.scale.setScalar(s);
    dummy.updateMatrix();
    mesh.setMatrixAt(i, dummy.matrix);

    const [r, g, b] = nodeColor(n, highlightedIds, 1.0, nodeBoost, tempColor);
    colorArr[i*3] = r; colorArr[i*3+1] = g; colorArr[i*3+2] = b;
  }
  // 关键：用 instancedBufferAttribute 挂到 geometry，而不是 instanceColor
  // （与参考 NodeSpheres 一致：geometry-attributes-color）
  geo.setAttribute('color', new THREE.InstancedBufferAttribute(colorArr, 3));
  mesh.instanceMatrix.needsUpdate = true;
  mesh.computeBoundingSphere();
  mesh.userData.nodeList = nodes;
  nodeMesh = mesh;
  scene.add(nodeMesh);
}

function buildNodePoints(nodes, hasHighlight, nodeBoost, tempColor) {
  const count = nodes.length;
  const positions = new Float32Array(count * 3);
  const colors = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const n = nodes[i];
    positions[i*3] = n.x;
    positions[i*3+1] = n.y;
    positions[i*3+2] = n.z;
    const [r, g, b] = nodeColor(n, highlightedIds, 1.0, nodeBoost, tempColor);
    colors[i*3] = r; colors[i*3+1] = g; colors[i*3+2] = b;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  const mat = new THREE.PointsMaterial({
    vertexColors: true,
    size: 4,
    sizeAttenuation: true,
    map: getPointSprite(),
    alphaTest: 0.35,
    transparent: true,
    toneMapped: false,
  });
  const pts = new THREE.Points(geo, mat);
  pts.userData.nodeList = nodes;
  nodeMesh = pts;
  scene.add(nodeMesh);
}

// ════════════════════════════════════════════════════════
// §6 边线（1:1 照搬 EdgeLines.tsx — 加性混合 LineSegments）
// ════════════════════════════════════════════════════════

function getClusterKey(fp) {
  if (!fp) return '';
  const parts = fp.replace(/\\/g, '/').split('/');
  return parts.slice(0, Math.min(2, parts.length)).join('/');
}

function buildEdgeLines(nodes, edges) {
  if (edgeLines) {
    scene.remove(edgeLines);
    edgeLines.geometry.dispose();
    edgeLines.material.dispose();
    edgeLines = null;
  }
  if (!edges || edges.length === 0) return;

  const densityScale = edgeIntensityScale(edges.length) * display.edgeBrightness;
  const srcMap = new Map();
  for (let i = 0; i < nodes.length; i++) srcMap.set(nodes[i].id, i);

  const hasHighlight = highlightedIds && highlightedIds.size > 0;
  const positions = new Float32Array(edges.length * 6);
  const colors = new Float32Array(edges.length * 6);
  let validCount = 0;
  const tempColor = new THREE.Color();

  for (const edge of edges) {
    const si = srcMap.get(edge.source);
    const ti = srcMap.get(edge.target);
    if (si === undefined || ti === undefined) continue;

    const s = nodes[si];
    const t = nodes[ti];

    const sHL = !hasHighlight || highlightedIds.has(s.id);
    const tHL = !hasHighlight || highlightedIds.has(t.id);
    if (hasHighlight && !sHL && !tHL) continue;

    const sameCluster = getClusterKey(s.file_path) === getClusterKey(t.file_path);

    // intensity（1:1 照搬 EdgeLines）
    let intensity;
    if (hasHighlight) {
      intensity = (sHL && tHL) ? 0.5 : 0.04 * densityScale;
    } else {
      intensity = (sameCluster ? 0.25 : 0.06) * densityScale;
    }

    const edgeColor = new THREE.Color(EDGE_TYPE_COLORS[edge.type] || DEFAULT_EDGE_COLOR);
    const off = validCount * 6;
    positions[off]   = s.x; positions[off+1] = s.y; positions[off+2] = s.z;
    positions[off+3] = t.x; positions[off+4] = t.y; positions[off+5] = t.z;
    const r = edgeColor.r * intensity, g = edgeColor.g * intensity, b = edgeColor.b * intensity;
    colors[off]   = r; colors[off+1] = g; colors[off+2] = b;
    colors[off+3] = r; colors[off+4] = g; colors[off+5] = b;
    validCount++;
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions.slice(0, validCount * 6), 3));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(colors.slice(0, validCount * 6), 3));

  const mat = new THREE.LineBasicMaterial({
    vertexColors: true,
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    toneMapped: false,
  });
  edgeLines = new THREE.LineSegments(geo, mat);
  scene.add(edgeLines);
}

// ════════════════════════════════════════════════════════
// §7 标签（1:1 照搬 NodeLabels.tsx — Canvas 纹理 sprite）
// ════════════════════════════════════════════════════════

const TEXTURE_FONT_SIZE = 64;
const TEXTURE_FONT = `600 ${TEXTURE_FONT_SIZE}px Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
const TEXTURE_MAX_TEXT_WIDTH = 720;
const TEXTURE_PADDING_X = 24;
const TEXTURE_PADDING_Y = 14;
const TEXTURE_STROKE_WIDTH = 8;

function fitText(ctx, text, maxWidth) {
  if (ctx.measureText(text).width <= maxWidth) return text;
  let lo = 0, hi = text.length;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    const cand = text.slice(0, mid) + '...';
    if (ctx.measureText(cand).width <= maxWidth) lo = mid;
    else hi = mid - 1;
  }
  return text.slice(0, Math.max(1, lo)) + '...';
}

function createLabelTexture(name, color) {
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  ctx.font = TEXTURE_FONT;
  const text = fitText(ctx, name, TEXTURE_MAX_TEXT_WIDTH);
  const textWidth = Math.ceil(ctx.measureText(text).width);
  const logicalWidth = Math.max(1, textWidth + TEXTURE_PADDING_X * 2 + TEXTURE_STROKE_WIDTH * 2);
  const logicalHeight = TEXTURE_FONT_SIZE + TEXTURE_PADDING_Y * 2 + TEXTURE_STROKE_WIDTH * 2;
  const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.ceil(logicalWidth * pixelRatio);
  canvas.height = Math.ceil(logicalHeight * pixelRatio);
  canvas.style.width = logicalWidth + 'px';
  canvas.style.height = logicalHeight + 'px';

  ctx.scale(pixelRatio, pixelRatio);
  ctx.font = TEXTURE_FONT;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.lineJoin = 'round';
  ctx.lineWidth = TEXTURE_STROKE_WIDTH;
  ctx.strokeStyle = 'rgba(0,0,0,0.9)';
  ctx.fillStyle = color;

  const x = logicalWidth / 2, y = logicalHeight / 2;
  ctx.strokeText(text, x, y);
  ctx.fillText(text, x, y);

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.minFilter = THREE.LinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.generateMipmaps = false;
  texture.needsUpdate = true;
  return { texture, width: logicalWidth, height: logicalHeight };
}

function buildLabels(nodes) {
  if (labelGroup) {
    for (const s of labelSprites) {
      scene.remove(s);
      if (s.material.map) s.material.map.dispose();
      s.material.dispose();
    }
    labelSprites = [];
    labelGroup = null;
  }

  const maxLabels = 80;
  const hasHighlight = highlightedIds && highlightedIds.size > 0;
  let labeled;
  if (hasHighlight) {
    labeled = nodes
      .filter(n => highlightedIds.has(n.id))
      .sort((a, b) => b.size - a.size)
      .slice(0, maxLabels);
  } else {
    labeled = [...nodes].sort((a, b) => b.size - a.size).slice(0, maxLabels);
  }

  for (const n of labeled) {
    const label = createLabelTexture(n.name, n.color);
    if (!label) continue;
    const mat = new THREE.SpriteMaterial({
      map: label.texture, transparent: true, depthWrite: false, toneMapped: false,
    });
    const sprite = new THREE.Sprite(mat);
    // 1:1 照搬 NodeLabelSprite 缩放公式
    const worldFontSize = Math.max(1.8, n.size * 0.4);
    const worldHeight = worldFontSize * (label.height / TEXTURE_FONT_SIZE);
    const worldWidth = worldHeight * (label.width / label.height);
    sprite.position.set(n.x, n.y + n.size * 0.7 + worldHeight / 2, n.z);
    sprite.scale.set(worldWidth, worldHeight, 1);
    sprite.renderOrder = 20;
    sprite.frustumCulled = false;
    scene.add(sprite);
    labelSprites.push(sprite);
  }
}

// ════════════════════════════════════════════════════════
// §4 续：渲染循环 + 相机动画 + idle 旋转（1:1 照搬 GraphScene）
// ════════════════════════════════════════════════════════

function animate() {
  requestAnimationFrame(animate);

  // CameraAnimator（1:1 照搬 ease-out cubic lerp + controls.target.lerp）
  if (cameraTarget && cameraAnimProgress < 1) {
    cameraAnimProgress = Math.min(1, cameraAnimProgress + 0.02);
    const t = 1 - Math.pow(1 - cameraAnimProgress, 3);
    camera.position.lerp(cameraTarget.position, t * 0.08);
    if (controls) {
      controls.target.lerp(cameraTarget.lookAt, t * 0.08);
      controls.update();
    } else {
      camera.lookAt(cameraTarget.lookAt);
    }
  }

  // IdleAutoRotate（1:1 照搬 60s idle）
  if (controls && Date.now() - lastInteraction > IDLE_TIMEOUT_MS) {
    controls.autoRotate = true;
  }

  if (controls) controls.update();

  // 更新 bloom intensity（1:1 照搬 GraphScene bloomIntensity）
  if (bloomPass && filteredData) {
    const bi = BASE_BLOOM_INTENSITY * bloomIntensityScale(filteredData.nodes.length) * display.bloom;
    bloomPass.intensity = bi;
  }

  // 渲染：用 composer 走 bloom，不用直接 renderer.render
  if (composer) composer.render();
  else renderer.render(scene, camera);
}

// 1:1 照搬 GraphScene.computeCameraTarget
function computeCameraTarget(nodes, ids) {
  if (!nodes || nodes.length === 0) return null;
  const targetNodes = ids ? nodes.filter(n => ids.has(n.id)) : nodes;
  if (targetNodes.length === 0) return null;

  let cx = 0, cy = 0, cz = 0, count = 0;
  for (const n of targetNodes) { cx += n.x; cy += n.y; cz += n.z; count++; }
  if (count === 0) return null;
  cx /= count; cy /= count; cz /= count;

  let maxDist = 0;
  for (const n of targetNodes) {
    const d = Math.sqrt((n.x - cx)**2 + (n.y - cy)**2 + (n.z - cz)**2);
    if (d > maxDist) maxDist = d;
  }
  const spreadDist = maxDist * 3;
  const minDist = count <= 5 ? 300 : 200;
  const distance = Math.max(minDist, spreadDist);
  return {
    position: new THREE.Vector3(cx + distance * 0.2, cy + distance * 0.15, cz + distance),
    lookAt: new THREE.Vector3(cx, cy, cz),
  };
}

// ════════════════════════════════════════════════════════
// §8 交互（1:1 照搬 GraphTab handleNodeClick + NodeTooltip）
// ════════════════════════════════════════════════════════

function onPointerMove(event) {
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

  if (!nodeMesh || !filteredData) return;
  raycaster.setFromCamera(pointer, camera);

  let idx = -1;
  if (nodeMode === 'spheres' && nodeMesh.isInstancedMesh) {
    const hits = raycaster.intersectObject(nodeMesh);
    if (hits.length > 0 && hits[0].instanceId !== undefined) idx = hits[0].instanceId;
  } else if (nodeMode === 'points' && nodeMesh.isPoints) {
    const hits = raycaster.intersectObject(nodeMesh);
    if (hits.length > 0 && hits[0].index !== undefined) idx = hits[0].index;
  }

  if (idx >= 0 && idx < nodeMesh.userData.nodeList.length) {
    hoveredNode = nodeMesh.userData.nodeList[idx];
    showTooltip(hoveredNode, event.clientX, event.clientY);
    renderer.domElement.style.cursor = 'pointer';
    return;
  }
  hoveredNode = null;
  hideTooltip();
  renderer.domElement.style.cursor = 'default';
}

function onPointerClick() {
  if (!hoveredNode) return;
  handleNodeClick(hoveredNode);
}

// 1:1 照搬 GraphTab.handleNodeClick
function handleNodeClick(node) {
  if (!filteredData) return;
  selectedNode = node;
  const connectedIds = new Set([node.id]);
  for (const e of filteredData.edges) {
    if (e.source === node.id) connectedIds.add(e.target);
    if (e.target === node.id) connectedIds.add(e.source);
  }
  highlightedIds = connectedIds;
  cameraTarget = computeCameraTarget(filteredData.nodes, connectedIds);
  cameraAnimProgress = 0;
  rebuildGraph();
  showDetailPanel(node);
}

// NodeTooltip（1:1 照搬 NodeTooltip.tsx）
function showTooltip(node, screenX, screenY) {
  const tt = document.getElementById('tooltip');
  const labelColor = deadCodeView ? colorForStatus(node.status) : colorForLabel(node.label);
  let html = '<div class="tt-name"><span class="tt-dot" style="background:' + labelColor + '"></span>';
  html += '<span class="tt-name-text">' + escapeHtml(node.name) + '</span>';
  html += '<span class="tt-label">' + escapeHtml(node.label) + '</span></div>';
  if (node.file_path) {
    const fp = node.file_path.replace(/\\/g, '/');
    const short = fp.split('/').slice(-2).join('/');
    let range = '';
    if (node.start_line) {
      range = (node.end_line && node.end_line !== node.start_line)
        ? 'L' + node.start_line + '-' + node.end_line
        : 'L' + node.start_line;
    }
    html += '<div class="tt-meta">' + escapeHtml(short) + (range ? ' · ' + range : '') + '</div>';
  }
  if (node.status && node.status !== 'structural') {
    const sc = colorForStatus(node.status);
    html += '<div class="tt-status"><span class="tt-dot sm" style="background:' + sc + '"></span>';
    html += escapeHtml(node.status);
    if (node.in_calls !== undefined) {
      html += '<span class="tt-calls"> · ' + node.in_calls + ' caller' + (node.in_calls === 1 ? '' : 's') + '</span>';
    }
    html += '</div>';
  }
  html += '<div class="tt-hint">click for code →</div>';
  tt.innerHTML = html;
  tt.style.display = 'block';
  tt.style.left = (screenX + 14) + 'px';
  tt.style.top = (screenY + 14) + 'px';
}
function hideTooltip() {
  const tt = document.getElementById('tooltip');
  if (tt) tt.style.display = 'none';
}

function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

// ════════════════════════════════════════════════════════
// §9 详情面板（1:1 照搬 NodeDetailPanel.tsx）
// ════════════════════════════════════════════════════════

function lineSuffix(node) {
  if (!node.start_line) return '';
  const end = (node.end_line && node.end_line !== node.start_line) ? '-L' + node.end_line : '';
  return '#L' + node.start_line + end;
}
function encodePath(p) {
  return p.split('/').map(encodeURIComponent).join('/');
}
function githubUrl(node) {
  if (!repoInfo || !repoInfo.blob_base || !node.file_path) return null;
  return repoInfo.blob_base + '/' + encodePath(node.file_path) + lineSuffix(node);
}

function showDetailPanel(node) {
  const panel = document.getElementById('right-panel');
  const header = document.getElementById('detail-header');
  const body = document.getElementById('detail-body');
  const labelColor = deadCodeView ? colorForStatus(node.status) : colorForLabel(node.label);

  let h = '<div class="dh-top"><div class="dh-title-wrap">';
  h += '<span class="dh-dot" style="background:' + labelColor + '"></span>';
  h += '<h2>' + escapeHtml(node.name) + '</h2>';
  h += '<span class="dh-badge" style="background:' + labelColor + '22;color:' + labelColor + '">' + escapeHtml(node.label) + '</span>';
  h += '</div><button class="dh-close" id="btn-close-detail">×</button></div>';
  if (node.file_path) {
    h += '<p class="dh-path">' + escapeHtml(node.file_path);
    if (node.start_line) {
      h += '<span class="dh-line"> :' + node.start_line;
      if (node.end_line && node.end_line !== node.start_line) h += '-' + node.end_line;
      h += '</span>';
    }
    h += '</p>';
  }
  // 代码按钮 + git 链接
  const ghUrl = githubUrl(node);
  h += '<div class="dh-actions">';
  h += '<button id="btn-show-code">查看源码</button>';
  if (ghUrl) {
    h += '<a class="dh-git" href="' + ghUrl + '" target="_blank" rel="noopener">在 Git 中查看 ↗</a>';
  }
  h += '</div>';
  h += '<div id="source-area"></div>';
  header.innerHTML = h;

  // 连接关系（1:1 照搬 NodeDetailPanel connections — 前端算）
  const nodeById = new Map(filteredData.nodes.map(n => [n.id, n]));
  const outbound = {}, inbound = {};
  for (const e of filteredData.edges) {
    if (e.source === node.id) {
      const t = nodeById.get(e.target);
      if (t) { (outbound[e.type] = outbound[e.type] || []).push(t); }
    }
    if (e.target === node.id) {
      const s = nodeById.get(e.source);
      if (s) { (inbound[e.type] = inbound[e.type] || []).push(s); }
    }
  }
  const outCount = Object.values(outbound).reduce((a, b) => a + b.length, 0);
  const inCount = Object.values(inbound).reduce((a, b) => a + b.length, 0);

  let b = '<div class="dh-stats">';
  b += '<div class="dh-stat"><span class="dh-stat-label">出边</span><span class="dh-stat-val out">' + outCount + '</span></div>';
  b += '<div class="dh-stat"><span class="dh-stat-label">入边</span><span class="dh-stat-val in">' + inCount + '</span></div>';
  b += '<div class="dh-stat"><span class="dh-stat-label">总数</span><span class="dh-stat-val">' + (outCount + inCount) + '</span></div>';
  b += '</div>';

  b += '<div class="detail-body-inner">';
  b += renderConnSection('引用 (Outbound)', '→', outbound);
  b += renderConnSection('被引用 (Inbound)', '←', inbound);
  b += '</div>';

  body.innerHTML = b;
  panel.classList.add('active');

  // 绑定事件
  document.getElementById('btn-close-detail').onclick = clearSelection;
  document.getElementById('btn-show-code').onclick = () => loadSource(node.id);
  // 连接项点击
  body.querySelectorAll('.conn-item').forEach(el => {
    el.onclick = () => {
      const id = parseInt(el.dataset.nodeId, 10);
      const n = nodeById.get(id);
      if (n) handleNodeClick(n);
    };
  });
  // 调整右面板宽度
  panel.style.width = rightWidth + 'px';
}

function renderConnSection(title, icon, grouped) {
  const total = Object.values(grouped).reduce((a, b) => a + b.length, 0);
  let h = '<div class="detail-section"><p class="ds-title">' + title;
  h += ' <span class="ds-count">(' + total + ')</span></p>';
  if (total === 0) {
    h += '<p class="ds-empty">无</p></div>';
    return h;
  }
  // 按 type 分组，按数量降序
  const entries = Object.entries(grouped).sort((a, b) => b[1].length - a[1].length);
  for (const [type, items] of entries) {
    const ec = EDGE_TYPE_COLORS[type] || DEFAULT_EDGE_COLOR;
    h += '<div class="ds-group"><p class="ds-group-title" style="color:' + ec + '">'
      + type.replace(/_/g, ' ').toLowerCase() + ' (' + items.length + ')</p>';
    for (const item of items.slice(0, 25)) {
      const ic = deadCodeView ? colorForStatus(item.status) : colorForLabel(item.label);
      h += '<div class="conn-item" data-node-id="' + item.id + '">'
        + '<span class="conn-icon">' + icon + '</span>'
        + '<span class="conn-dot" style="background:' + ic + '"></span>'
        + '<span class="conn-name">' + escapeHtml(item.name) + '</span>'
        + '<span class="conn-label">' + escapeHtml(item.label) + '</span>'
        + '</div>';
    }
    if (items.length > 25) {
      h += '<p class="ds-more">+' + (items.length - 25) + ' more</p>';
    }
    h += '</div>';
  }
  h += '</div>';
  return h;
}

async function loadSource(id) {
  const area = document.getElementById('source-area');
  if (!area) return;
  area.innerHTML = '<div class="src-loading">加载中...</div>';
  try {
    const res = await fetch('/api/source/' + id + '?context=3');
    const data = await res.json();
    if (data.error) {
      area.innerHTML = '<div class="src-error">' + escapeHtml(data.error) + '</div>';
      return;
    }
    const lines = data.source.split('\n');
    let html = '<div class="src-meta">' + escapeHtml(data.file_path)
      + ' L' + data.node_start + '-' + data.node_end + '</div><div class="source">';
    lines.forEach((line, i) => {
      const ln = data.start_line + i;
      const hl = (ln >= data.node_start && ln <= data.node_end) ? ' hl' : '';
      const esc = line.replace(/</g, '&lt;').replace(/>/g, '&gt;');
      html += '<div class="src-line' + hl + '"><span class="src-ln">' + String(ln).padStart(4) + '</span><span class="src-code">' + esc + '</span></div>';
    });
    html += '</div>';
    area.innerHTML = html;
  } catch (e) {
    area.innerHTML = '<div class="src-error">' + escapeHtml(String(e)) + '</div>';
  }
}

function clearSelection() {
  selectedNode = null;
  highlightedIds = null;
  cameraTarget = null;
  cameraAnimProgress = 1;
  document.getElementById('right-panel').classList.remove('active');
  rebuildGraph();
  updateClearButton();
}

// ════════════════════════════════════════════════════════
// §10 文件树（1:1 照搬 Sidebar.tsx — buildFileTree + flattenSingleChild）
// ════════════════════════════════════════════════════════

function buildFileTree(nodes) {
  const root = { name: '/', fullPath: '', children: new Map(), nodeIds: new Set(), directNodes: [] };
  for (const node of nodes) {
    if (!node.file_path) continue;
    const parts = node.file_path.replace(/\\/g, '/').split('/');
    let cur = root;
    for (let i = 0; i < parts.length - 1; i++) {
      if (!parts[i]) continue;
      let child = cur.children.get(parts[i]);
      if (!child) {
        const prefix = parts.slice(0, i + 1).join('/');
        child = { name: parts[i], fullPath: prefix, children: new Map(), nodeIds: new Set(), directNodes: [] };
        cur.children.set(parts[i], child);
      }
      cur = child;
    }
    cur.directNodes.push(node);
  }
  function collect(d) {
    const ids = new Set();
    for (const n of d.directNodes) ids.add(n.id);
    for (const c of d.children.values()) for (const id of collect(c)) ids.add(id);
    d.nodeIds = ids;
    return ids;
  }
  collect(root);
  return root;
}

function flattenSingleChild(dir) {
  const children = new Map();
  for (const [key, child] of dir.children) {
    let flat = flattenSingleChild(child);
    while (flat.children.size === 1 && flat.directNodes.length === 0) {
      const [sk, sc] = [...flat.children.entries()][0];
      flat = { ...sc, name: flat.name + '/' + sk, children: flattenSingleChild(sc).children };
    }
    children.set(key, flat);
  }
  return { ...dir, children };
}

function renderFileTree(nodes) {
  const container = document.getElementById('file-tree');
  container.innerHTML = '';
  const tree = flattenSingleChild(buildFileTree(nodes));
  const topLevel = [...tree.children.values()].sort((a, b) => a.name.localeCompare(b.name));
  const search = document.getElementById('search-input').value.trim().toLowerCase();
  if (search) {
    const filtered = nodes.filter(n =>
      (n.name || '').toLowerCase().includes(search) || (n.file_path || '').toLowerCase().includes(search)
    ).slice(0, 50);
    if (filtered.length === 0) {
      container.innerHTML = '<p class="ft-empty">无匹配</p>';
    } else {
      for (const n of filtered) {
        const el = document.createElement('button');
        el.className = 'ft-leaf';
        el.innerHTML = '<span class="ft-dot" style="background:' + n.color + '"></span>'
          + '<span class="ft-name">' + escapeHtml(n.name) + '</span>'
          + '<span class="ft-path">' + escapeHtml(n.file_path || '') + '</span>';
        el.onclick = () => handleSelectPath(n.file_path || '', new Set([n.id]));
        container.appendChild(el);
      }
    }
    return;
  }
  for (const c of topLevel) renderTreeItem(container, c, '', 0);
}

function renderTreeItem(container, dir, parentPath, depth) {
  const item = document.createElement('div');
  item.className = 'tree-item';
  item.dataset.path = dir.fullPath;
  const indent = depth * 16 + 12;
  item.style.paddingLeft = indent + 'px';
  const arrow = (dir.children.size > 0 || dir.directNodes.length > 0) ? '▸' : '';
  item.innerHTML = '<span class="ti-arrow">' + arrow + '</span>'
    + '<span class="ti-name">' + escapeHtml(dir.name) + '</span>'
    + '<span class="ti-count">' + dir.nodeIds.size + '</span>';
  let expanded = false;
  const childContainer = document.createElement('div');
  item.onclick = (e) => {
    e.stopPropagation();
    expanded = !expanded;
    item.classList.toggle('expanded', expanded);
    item.querySelector('.ti-arrow').textContent = expanded ? '▾' : arrow;
    if (expanded) {
      childContainer.innerHTML = '';
      const sorted = [...dir.children.values()].sort((a, b) => a.name.localeCompare(b.name));
      for (const c of sorted) renderTreeItem(childContainer, c, dir.fullPath, depth + 1);
      const sortedNodes = [...dir.directNodes].sort((a, b) => a.name.localeCompare(b.name));
      for (const gn of sortedNodes) {
        const leaf = document.createElement('button');
        leaf.className = 'tree-leaf';
        leaf.style.paddingLeft = ((depth + 1) * 16 + 12) + 'px';
        leaf.innerHTML = '<span class="ft-dot" style="background:' + gn.color + '"></span>'
          + '<span class="ft-leaf-name">' + escapeHtml(gn.name) + '</span>'
          + '<span class="ft-leaf-label">' + escapeHtml(gn.label) + '</span>';
        leaf.onclick = (ev) => {
          ev.stopPropagation();
          handleSelectPath(dir.fullPath + '/' + gn.name, new Set([gn.id]));
        };
        childContainer.appendChild(leaf);
      }
    } else {
      childContainer.innerHTML = '';
    }
    // 高亮该目录
    handleSelectPath(dir.fullPath, dir.nodeIds);
  };
  container.appendChild(item);
  container.appendChild(childContainer);
}

// 1:1 照搬 GraphTab.handleSelectPath
function handleSelectPath(path, nodeIds) {
  if (!filteredData || !path || nodeIds.size === 0) {
    highlightedIds = null;
    cameraTarget = null;
    cameraAnimProgress = 1;
    rebuildGraph();
    updateClearButton();
    return;
  }
  highlightedIds = nodeIds;
  cameraTarget = computeCameraTarget(filteredData.nodes, nodeIds);
  cameraAnimProgress = 0;
  rebuildGraph();
  updateClearButton();
}

// ════════════════════════════════════════════════════════
// §11 过滤面板（1:1 照搬 FilterPanel.tsx）
// ════════════════════════════════════════════════════════

function renderFilters() {
  if (!graphData) return;

  // 节点类型
  const labelCounts = new Map();
  for (const n of graphData.nodes) labelCounts.set(n.label, (labelCounts.get(n.label) || 0) + 1);
  const kindDiv = document.getElementById('kind-filters');
  kindDiv.innerHTML = '';
  for (const [label, count] of [...labelCounts.entries()].sort((a, b) => b[1] - a[1])) {
    const c = colorForLabel(label);
    const on = enabledLabels.has(label);
    const el = document.createElement('button');
    el.className = 'filter-chip' + (on ? ' on' : '');
    el.innerHTML = '<span class="fc-dot" style="background:' + (on ? c : '#444') + '"></span>'
      + '<span class="fc-label" style="color:' + (on ? c : '#555') + '">' + escapeHtml(label) + '</span>'
      + '<span class="fc-count">' + count + '</span>';
    el.onclick = () => { toggleLabel(label); };
    kindDiv.appendChild(el);
  }

  // 边类型
  const edgeCounts = new Map();
  for (const e of graphData.edges) edgeCounts.set(e.type, (edgeCounts.get(e.type) || 0) + 1);
  const edgeDiv = document.getElementById('edge-filters');
  edgeDiv.innerHTML = '';
  for (const [type, count] of [...edgeCounts.entries()].sort((a, b) => b[1] - a[1])) {
    const on = enabledEdgeTypes.has(type);
    const el = document.createElement('button');
    el.className = 'filter-chip edge' + (on ? ' on' : '');
    el.innerHTML = '<span class="fc-label">' + escapeHtml(type.replace(/_/g, ' ').toLowerCase()) + '</span>'
      + '<span class="fc-count">' + count + '</span>';
    el.onclick = () => { toggleEdgeType(type); };
    edgeDiv.appendChild(el);
  }

  // dead-code 统计
  let deadCount = 0;
  for (const n of graphData.nodes) if (n.status === 'dead') deadCount++;
  document.getElementById('dead-count').textContent = deadCount + ' dead';

  // legend
  const legend = document.getElementById('status-legend');
  if (deadCodeView) {
    legend.style.display = 'flex';
    legend.innerHTML = STATUS_LEGEND.map(s =>
      '<span class="legend-item"><span class="legend-dot" style="background:' + s.color + '"></span>' + escapeHtml(s.label) + '</span>'
    ).join('');
  } else {
    legend.style.display = 'none';
  }
}

function toggleLabel(label) {
  const next = new Set(enabledLabels);
  if (next.has(label)) next.delete(label); else next.add(label);
  enabledLabels = next;
  applyFilters();
}
function toggleEdgeType(type) {
  const next = new Set(enabledEdgeTypes);
  if (next.has(type)) next.delete(type); else next.add(type);
  enabledEdgeTypes = next;
  applyFilters();
}
function enableAllFilters() {
  if (!graphData) return;
  enabledLabels = new Set(graphData.nodes.map(n => n.label));
  enabledEdgeTypes = new Set(graphData.edges.map(e => e.type));
  applyFilters();
}
function disableAllFilters() {
  enabledLabels = new Set();
  enabledEdgeTypes = new Set();
  applyFilters();
}

// ════════════════════════════════════════════════════════
// §12 图控制器（1:1 照搬 GraphTab — filter + rebuild + HUD）
// ════════════════════════════════════════════════════════

function applyFilters() {
  if (!graphData) return;

  // 1:1 照搬 filteredData memo
  const statusOk = (n) => {
    if (showOnlyDead && n.status !== 'dead') return false;
    if (hideEntryPoints && n.status === 'entry') return false;
    if (hideTests && n.status === 'test') return false;
    return true;
  };
  const paint = (n) => deadCodeView ? { ...n, color: colorForStatus(n.status) } : n;
  const keep = (n) => enabledLabels.has(n.label) && statusOk(n);

  const filteredNodes = graphData.nodes.filter(keep).map(paint);
  const nodeIdSet = new Set(filteredNodes.map(n => n.id));
  const filteredEdges = graphData.edges.filter(e =>
    enabledEdgeTypes.has(e.type) && nodeIdSet.has(e.source) && nodeIdSet.has(e.target)
  );
  filteredData = { nodes: filteredNodes, edges: filteredEdges };

  rebuildGraph();
  renderFileTree(filteredNodes);
  renderFilters(); // 重渲染 chip（更新 on 状态）
  updateHUD();
}

function rebuildGraph() {
  if (!filteredData) return;
  buildNodeCloud(filteredData.nodes);
  buildEdgeLines(filteredData.nodes, filteredData.edges);
  buildLabels(showLabels ? filteredData.nodes : []);
}

function updateHUD() {
  if (!filteredData || !graphData) return;
  const main = document.getElementById('hud-main');
  const filt = document.getElementById('hud-filtered');
  const notice = document.getElementById('hud-notice');
  const sel = document.getElementById('hud-selected');
  if (main) {
    main.textContent = filteredData.nodes.length + ' nodes / ' + filteredData.edges.length + ' edges';
  }
  if (filt) {
    if (graphData.nodes.length > filteredData.nodes.length) {
      filt.textContent = 'filtered from ' + graphData.nodes.length;
    } else filt.textContent = '';
  }
  if (notice) {
    notice.textContent = (graphData.total_nodes > graphData.nodes.length)
      ? 'Showing ' + graphData.nodes.length + ' of ' + graphData.total_nodes + ' nodes — raise budget for more'
      : '';
  }
  if (sel) {
    sel.textContent = (highlightedIds && highlightedIds.size > 0)
      ? highlightedIds.size + ' selected' : '';
  }
}

function updateClearButton() {
  const btn = document.getElementById('btn-clear-sel');
  const wrap = document.getElementById('clear-sel-wrap');
  const show = highlightedIds && highlightedIds.size > 0;
  if (btn) btn.style.display = show ? 'inline-block' : 'none';
  if (wrap) wrap.style.display = show ? 'block' : 'none';
}

// ════════════════════════════════════════════════════════
// §13 数据获取（1:1 照搬 useGraphData.fetchLayout）
// ════════════════════════════════════════════════════════

async function fetchGraph() {
  const loading = document.getElementById('loading');
  if (loading) loading.style.display = 'block';
  const budgetVal = clampNodeBudget(parseInt(document.getElementById('budget-input').value, 10));
  document.getElementById('budget-input').value = budgetVal;
  try {
    const [layoutRes, repoRes] = await Promise.all([
      fetch('/api/layout?max_nodes=' + budgetVal),
      fetch('/api/repo-info'),
    ]);
    if (!layoutRes.ok) {
      const e = await layoutRes.json().catch(() => ({ error: layoutRes.statusText }));
      throw new Error(e.error || ('HTTP ' + layoutRes.status));
    }
    graphData = await layoutRes.json();
    repoInfo = await repoRes.json();
    if (repoInfo && repoInfo.error) repoInfo = null;
    enabledLabels = new Set(graphData.nodes.map(n => n.label));
    enabledEdgeTypes = new Set(graphData.edges.map(e => e.type));
    applyFilters();
  } catch (e) {
    const hud = document.getElementById('hud');
    if (hud) hud.textContent = 'Error: ' + e.message;
    const err = document.getElementById('load-error');
    if (err) {
      err.textContent = e.message;
      err.style.display = 'block';
    }
  } finally {
    if (loading) loading.style.display = 'none';
  }
}

// ════════════════════════════════════════════════════════
// §14 显示设置 + 搜索 + resize + 启动
// ════════════════════════════════════════════════════════

function toggleDisplayMenu() {
  document.getElementById('display-menu').classList.toggle('active');
}

function setupDisplaySliders() {
  const sliders = [
    ['slider-edge', 'val-edge', 'edgeBrightness'],
    ['slider-glow', 'val-glow', 'nodeGlow'],
    ['slider-bloom', 'val-bloom', 'bloom'],
  ];
  for (const [sliderId, valId, key] of sliders) {
    const slider = document.getElementById(sliderId);
    const val = document.getElementById(valId);
    slider.value = display[key];
    val.textContent = display[key].toFixed(2) + '×';
    slider.oninput = () => {
      const next = { ...display, [key]: parseFloat(slider.value) };
      display = next;
      saveDisplaySettings(next);
      val.textContent = display[key].toFixed(2) + '×';
      rebuildGraph();
      updateIsDefault();
    };
  }
  updateIsDefault();
}
function updateIsDefault() {
  const isDefault = display.edgeBrightness === 1 && display.nodeGlow === 1 && display.bloom === 1;
  const ind = document.getElementById('display-dot');
  if (ind) ind.style.display = isDefault ? 'none' : 'inline';
}
function resetDisplay() {
  display = { ...DEFAULT_DISPLAY_SETTINGS };
  saveDisplaySettings(display);
  setupDisplaySliders();
  rebuildGraph();
}

function setupDeadCodeToggles() {
  const toggles = [
    ['toggle-dead-view', 'cr-dead-view', v => { deadCodeView = v; }],
    ['toggle-only-dead', 'cr-only-dead', v => { showOnlyDead = v; }],
    ['toggle-hide-entry', 'cr-hide-entry', v => { hideEntryPoints = v; }],
    ['toggle-hide-tests', 'cr-hide-tests', v => { hideTests = v; }],
  ];
  for (const [id, rowId, setter] of toggles) {
    const el = document.getElementById(id);
    const row = document.getElementById(rowId);
    if (!el || !row) continue;
    el.checked = false;
    row.classList.toggle('on', false);
    row.addEventListener('click', (e) => {
      // 避免点击隐藏 input 触发两次
      if (e.target === el) return;
      el.checked = !el.checked;
      el.dispatchEvent(new Event('change'));
    });
    el.onchange = () => {
      row.classList.toggle('on', el.checked);
      setter(el.checked);
      applyFilters();
    };
  }

  // showLabels 开关
  const slEl = document.getElementById('toggle-show-labels');
  const slRow = document.getElementById('cr-show-labels');
  if (slEl && slRow) {
    slRow.classList.toggle('on', showLabels);
    slRow.addEventListener('click', (e) => {
      if (e.target === slEl) return;
      slEl.checked = !slEl.checked;
      slEl.dispatchEvent(new Event('change'));
    });
    slEl.onchange = () => {
      showLabels = slEl.checked;
      slRow.classList.toggle('on', showLabels);
      rebuildGraph();
    };
  }
}

function setupSearch() {
  const input = document.getElementById('search-input');
  if (!input) return;
  input.addEventListener('input', () => {
    if (filteredData) renderFileTree(filteredData.nodes);
  });
  input.addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    const name = input.value.trim().toLowerCase();
    if (!name || !filteredData) return;
    const matches = filteredData.nodes.filter(n => (n.name || '').toLowerCase().includes(name));
    if (matches.length === 0) return;
    const ids = new Set(matches.map(n => n.id));
    highlightedIds = ids;
    selectedNode = matches[0];
    cameraTarget = computeCameraTarget(filteredData.nodes, ids);
    cameraAnimProgress = 0;
    rebuildGraph();
    showDetailPanel(selectedNode);
    updateClearButton();
  });
}

function onResize() {
  const container = document.getElementById('canvas-container');
  if (!container || !camera || !renderer) return;
  const w = container.clientWidth, h = container.clientHeight;
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  renderer.setSize(w, h);
  if (composer) composer.setSize(w, h);
}

// 面板宽度拖拽（1:1 照搬 ResizeHandle + GraphTab loadWidth/saveWidth）
function setupResizeHandles() {
  const leftHandle = document.getElementById('resize-left');
  const rightHandle = document.getElementById('resize-right');
  if (leftHandle) {
    let dragging = false, lastX = 0;
    leftHandle.addEventListener('pointerdown', e => {
      dragging = true; lastX = e.clientX;
      leftHandle.setPointerCapture(e.pointerId);
    });
    leftHandle.addEventListener('pointermove', e => {
      if (!dragging) return;
      const d = e.clientX - lastX; lastX = e.clientX;
      leftWidth = Math.max(150, Math.min(500, leftWidth + d));
      saveWidth('shaderbase-left-w', leftWidth);
      document.getElementById('left-panel').style.width = leftWidth + 'px';
      onResize();
    });
    leftHandle.addEventListener('pointerup', () => { dragging = false; });
  }
  if (rightHandle) {
    let dragging = false, lastX = 0;
    rightHandle.addEventListener('pointerdown', e => {
      dragging = true; lastX = e.clientX;
      rightHandle.setPointerCapture(e.pointerId);
    });
    rightHandle.addEventListener('pointermove', e => {
      if (!dragging) return;
      const d = e.clientX - lastX; lastX = e.clientX;
      rightWidth = Math.max(200, Math.min(500, rightWidth - d));
      saveWidth('shaderbase-right-w', rightWidth);
      const panel = document.getElementById('right-panel');
      if (panel.classList.contains('active')) panel.style.width = rightWidth + 'px';
      onResize();
    });
    rightHandle.addEventListener('pointerup', () => { dragging = false; });
  }
}

// ════════════════════════════════════════════════════════
// 启动
// ════════════════════════════════════════════════════════

window.addEventListener('DOMContentLoaded', () => {
  // 应用持久化的面板宽度
  document.getElementById('left-panel').style.width = leftWidth + 'px';
  initScene();
  setupDisplaySliders();
  setupDeadCodeToggles();
  setupSearch();
  setupResizeHandles();

  // 持久化 budget
  const persisted = loadNodeBudget(currentProject);
  budget = { project: currentProject, value: persisted };
  budgetDraft = String(persisted);
  document.getElementById('budget-input').value = persisted;

  fetchGraph();
});

// 全局按钮
window.fetchGraph = fetchGraph;
window.toggleDisplayMenu = toggleDisplayMenu;
window.resetDisplay = resetDisplay;
window.enableAllFilters = enableAllFilters;
window.disableAllFilters = disableAllFilters;
window.clearSelection = clearSelection;
