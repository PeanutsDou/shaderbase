// shaderbase 知识图谱 — 3D 星系可视化
// 1:1 复刻 codebase-memory graph-ui 的核心逻辑，vanilla JS + three.js

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/OrbitControls.js';

// ════════════════════════════════════════════════════════
// 1. 密度补偿（1:1 照搬 density.ts）
// ════════════════════════════════════════════════════════

const EDGE_REFERENCE_COUNT = 2500;
const NODE_REFERENCE_COUNT = 25000;
const NODE_FADE_END = 250000;

function edgeIntensityScale(edgeCount) {
  if (edgeCount <= EDGE_REFERENCE_COUNT) return 1;
  return Math.max(0.05, Math.sqrt(EDGE_REFERENCE_COUNT / edgeCount));
}

function fadeFactor(nodeCount) {
  if (nodeCount < NODE_REFERENCE_COUNT) return 0;
  if (nodeCount > NODE_FADE_END) return 1;
  return (nodeCount - NODE_REFERENCE_COUNT) / (NODE_FADE_END - NODE_REFERENCE_COUNT);
}

function bloomIntensityScale(nodeCount) {
  return 1 - fadeFactor(nodeCount) * (1 - 0.7);
}

function nodeBoostScale(nodeCount) {
  return 1 - fadeFactor(nodeCount) * (1 - 0.8);
}

function nodeGlowBoost(r, g, b) {
  const GLOW_BASE = 1.35;
  const blueness = Math.max(0, b - Math.max(r, g));
  const redness = Math.max(0, r - Math.max(g, b));
  return GLOW_BASE + blueness * 2.4 + redness * 0.9;
}

// ════════════════════════════════════════════════════════
// 2. 颜色映射（照搬 colors.ts + shaderbase kind）
// ════════════════════════════════════════════════════════

const LABEL_COLORS = {
  Function: '#06b6d4',
  Struct: '#22c55e',
  Uniform: '#f97316',
  Texture: '#3b82f6',
  SamplerState: '#e11d48',
  Technique: '#a855f7',
  CBuffer: '#eab308',
};

const EDGE_TYPE_COLORS = {
  CALLS: '#1DA27E',
  INCLUDES: '#3b82f6',
  HAS_MEMBER: '#22c55e',
  IS_ENTRY_POINT: '#a855f7',
  EXPOSES_TECHNIQUE: '#a855f7',
};
const DEFAULT_EDGE_COLOR = '#1C8585';

function colorForLabel(label) { return LABEL_COLORS[label] || '#94a3b8'; }
function hexToRGB(hex) {
  const c = new THREE.Color(hex);
  return [c.r, c.g, c.b];
}

// ════════════════════════════════════════════════════════
// 3. 全局状态
// ════════════════════════════════════════════════════════

let scene, camera, renderer, controls;
let nodeMesh, edgeLines, labelSprites = [];
let raycaster, pointer;
let graphData = null;
let filteredData = null;
let highlightedIds = null;
let selectedNode = null;
let hoveredNode = null;
let enabledLabels = new Set();
let enabledEdgeTypes = new Set();
let display = { edgeBrightness: 1.0, nodeGlow: 1.0, bloom: 1.0 };
let repoInfo = null;
let cameraTarget = null;
let cameraAnimProgress = 1;
let lastInteraction = Date.now();
let nodeMap = new Map(); // id → node data

const BASE_BLOOM_INTENSITY = 1.45;

// ════════════════════════════════════════════════════════
// 4. 3D 场景初始化（照搬 GraphScene.tsx）
// ════════════════════════════════════════════════════════

function initScene() {
  const container = document.getElementById('canvas-container');
  const w = container.clientWidth, h = container.clientHeight;

  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x06090f);

  camera = new THREE.PerspectiveCamera(50, w / h, 0.1, 100000);
  camera.position.set(0, 0, 800);

  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: 'high-performance' });
  renderer.setSize(w, h);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
  container.appendChild(renderer.domElement);

  // 灯照（照搬 GraphScene）
  scene.add(new THREE.AmbientLight(0xffffff, 0.5));
  const p1 = new THREE.PointLight(0xffffff, 0.6); p1.position.set(500, 500, 500); scene.add(p1);
  const p2 = new THREE.PointLight(0x6040ff, 0.4); p2.position.set(-300, -200, -300); scene.add(p2);

  // OrbitControls（照搬 GraphScene 参数）
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

  // 事件
  renderer.domElement.addEventListener('pointermove', onPointerMove);
  renderer.domElement.addEventListener('click', onPointerClick);
  renderer.domElement.addEventListener('pointerdown', () => { lastInteraction = Date.now(); controls.autoRotate = false; });
  renderer.domElement.addEventListener('wheel', () => { lastInteraction = Date.now(); });
  window.addEventListener('resize', onResize);

  animate();
}

// ════════════════════════════════════════════════════════
// 5. 节点云渲染（照搬 NodeCloud.tsx — InstancedMesh）
// ════════════════════════════════════════════════════════

function buildNodeCloud(nodes) {
  if (nodeMesh) { scene.remove(nodeMesh); nodeMesh.geometry.dispose(); nodeMesh.material.dispose(); }
  if (nodes.length === 0) return;

  const count = nodes.length;
  // 球体细分（照搬 sphereDetail）
  let ws, hs;
  if (count <= 8000) { ws = 32; hs = 24; }
  else if (count <= 25000) { ws = 16; hs = 12; }
  else { ws = 10; hs = 7; }

  const geo = new THREE.SphereGeometry(1, ws, hs);
  const mat = new THREE.MeshBasicMaterial({ toneMapped: false });
  nodeMesh = new THREE.InstancedMesh(geo, mat, count);
  nodeMesh.frustumCulled = false;
  nodeMesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(count * 3), 3);

  const dummy = new THREE.Object3D();
  const color = new THREE.Color();
  const tempColor = new THREE.Color();
  const nodeBoost = nodeBoostScale(count) * display.nodeGlow;
  const hasHighlight = highlightedIds && highlightedIds.size > 0;

  for (let i = 0; i < count; i++) {
    const n = nodes[i];
    dummy.position.set(n.x, n.y, n.z);
    const scale = n.size || 3;
    dummy.scale.setScalar(scale);
    dummy.updateMatrix();
    nodeMesh.setMatrixAt(i, dummy.matrix);

    // 颜色（照搬 nodeColor）
    tempColor.set(n.color || colorForLabel(n.label));
    if (hasHighlight && !highlightedIds.has(n.id)) {
      tempColor.multiplyScalar(0.15);
    } else {
      const [r, g, b] = [tempColor.r, tempColor.g, tempColor.b];
      const fullBoost = nodeGlowBoost(r, g, b);
      const applied = 1 + (fullBoost - 1) * nodeBoost;
      tempColor.multiplyScalar(applied);
    }
    nodeMesh.setColorAt(i, tempColor);
  }
  nodeMesh.instanceMatrix.needsUpdate = true;
  if (nodeMesh.instanceColor) nodeMesh.instanceColor.needsUpdate = true;
  nodeMesh.userData.nodeList = nodes;
  scene.add(nodeMesh);
}

// ════════════════════════════════════════════════════════
// 6. 边线渲染（照搬 EdgeLines.tsx — LineSegments + 加性混合）
// ════════════════════════════════════════════════════════

function buildEdgeLines(nodes, edges) {
  if (edgeLines) { scene.remove(edgeLines); edgeLines.geometry.dispose(); edgeLines.material.dispose(); }
  if (edges.length === 0) return;

  const nodeMap = new Map(nodes.map(n => [n.id, n]));
  const densityScale = edgeIntensityScale(edges.length) * display.edgeBrightness;
  const hasHighlight = highlightedIds && highlightedIds.size > 0;

  const positions = [];
  const colors = [];
  const tempColor = new THREE.Color();

  for (const e of edges) {
    const s = nodeMap.get(e.source);
    const t = nodeMap.get(e.target);
    if (!s || !t) continue;

    // 高亮过滤
    if (hasHighlight) {
      const sHL = highlightedIds.has(e.source);
      const tHL = highlightedIds.has(e.target);
      if (!sHL && !tHL) continue;
    }

    // intensity（照搬 EdgeLines）
    let intensity;
    if (hasHighlight) {
      const sHL = highlightedIds.has(e.source);
      const tHL = highlightedIds.has(e.target);
      intensity = (sHL && tHL) ? 0.5 : 0.04 * densityScale;
    } else {
      const sameCluster = getClusterKey(s.file_path) === getClusterKey(t.file_path);
      intensity = (sameCluster ? 0.25 : 0.06) * densityScale;
    }

    const edgeColor = EDGE_TYPE_COLORS[e.type] || DEFAULT_EDGE_COLOR;
    tempColor.set(edgeColor).multiplyScalar(intensity);

    positions.push(s.x, s.y, s.z, t.x, t.y, t.z);
    colors.push(tempColor.r, tempColor.g, tempColor.b, tempColor.r, tempColor.g, tempColor.b);
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));

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

function getClusterKey(fp) {
  if (!fp) return '';
  const parts = fp.replace(/\\/g, '/').split('/');
  return parts.slice(0, Math.min(2, parts.length)).join('/');
}

// ════════════════════════════════════════════════════════
// 7. 节点标签（照搬 NodeLabels.tsx — Canvas 纹理 sprite）
// ════════════════════════════════════════════════════════

function buildLabels(nodes) {
  // 清旧
  labelSprites.forEach(s => { scene.remove(s); s.material.map.dispose(); s.material.dispose(); });
  labelSprites = [];

  const maxLabels = 80;
  const sorted = [...nodes].sort((a, b) => (b.size || 0) - (a.size || 0));
  const candidates = highlightedIds
    ? sorted.filter(n => highlightedIds.has(n.id)).slice(0, maxLabels)
    : sorted.slice(0, maxLabels);

  for (const n of candidates) {
    const sprite = createLabelSprite(n.name || '?', n.color || colorForLabel(n.label));
    sprite.position.set(n.x, n.y + (n.size || 3) * 0.7 + 3, n.z);
    sprite.renderOrder = 20;
    scene.add(sprite);
    labelSprites.push(sprite);
  }
}

function createLabelSprite(text, color) {
  const fontSize = 64;
  const padding = 8;
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  ctx.font = `${fontSize}px "Segoe UI", sans-serif`;
  const textWidth = ctx.measureText(text).width;
  const maxTextWidth = 720;
  // fitText（照搬二分截断）
  let displayText = text;
  if (textWidth > maxTextWidth) {
    let lo = 0, hi = text.length;
    while (lo < hi) {
      const mid = Math.ceil((lo + hi) / 2);
      const w = ctx.measureText(text.slice(0, mid) + '...').width;
      if (w > maxTextWidth) hi = mid - 1; else lo = mid;
    }
    displayText = text.slice(0, lo) + '...';
  }
  const w = Math.ceil(ctx.measureText(displayText).width) + padding * 2;
  const h = fontSize + padding * 2;
  canvas.width = w; canvas.height = h;
  ctx.font = `${fontSize}px "Segoe UI", sans-serif`;
  ctx.textBaseline = 'middle';
  ctx.textAlign = 'center';
  // 描边
  ctx.strokeStyle = '#000000';
  ctx.lineWidth = padding;
  ctx.strokeText(displayText, w / 2, h / 2);
  // 填充
  ctx.fillStyle = color;
  ctx.fillText(displayText, w / 2, h / 2);

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.minFilter = THREE.LinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.generateMipmaps = false;

  const mat = new THREE.SpriteMaterial({ map: texture, depthWrite: false, toneMapped: false });
  const sprite = new THREE.Sprite(mat);
  const worldFontSize = 4;
  const aspect = h / w;
  sprite.scale.set(worldFontSize * w / h, worldFontSize, 1);
  return sprite;
}

// ════════════════════════════════════════════════════════
// 8. 渲染循环 + 相机动画 + idle 旋转（照搬 GraphScene）
// ════════════════════════════════════════════════════════

function animate() {
  requestAnimationFrame(animate);

  // CameraAnimator（照搬 ease-out cubic lerp）
  if (cameraTarget && cameraAnimProgress < 1) {
    cameraAnimProgress = Math.min(1, cameraAnimProgress + 0.02);
    const t = 1 - Math.pow(1 - cameraAnimProgress, 3);
    camera.position.lerp(cameraTarget.position, t * 0.08);
    controls.target.lerp(cameraTarget.lookAt, t * 0.08);
  }

  // IdleAutoRotate（60s）
  if (controls && Date.now() - lastInteraction > 60000) {
    controls.autoRotate = true;
  }

  controls.update();
  renderer.render(scene, camera);

  // Tooltip 跟随 hover
  updateTooltipPosition();
}

function computeCameraTarget(nodes, ids) {
  // 照搬 GraphScene.computeCameraTarget
  if (!nodes || nodes.length === 0) return null;
  const targetNodes = ids ? nodes.filter(n => ids.has(n.id)) : nodes;
  if (targetNodes.length === 0) return null;

  let cx = 0, cy = 0, cz = 0;
  for (const n of targetNodes) { cx += n.x; cy += n.y; cz += n.z; }
  cx /= targetNodes.length; cy /= targetNodes.length; cz /= targetNodes.length;

  let maxDist = 0;
  for (const n of targetNodes) {
    const d = Math.hypot(n.x - cx, n.y - cy, n.z - cz);
    if (d > maxDist) maxDist = d;
  }
  const minDist = targetNodes.length <= 5 ? 300 : 200;
  const distance = Math.max(minDist, maxDist * 3);

  return {
    position: new THREE.Vector3(cx + distance * 0.2, cy + distance * 0.15, cz + distance),
    lookAt: new THREE.Vector3(cx, cy, cz),
  };
}

// ════════════════════════════════════════════════════════
// 9. 交互（照搬 GraphTab handleNodeClick）
// ════════════════════════════════════════════════════════

function onPointerMove(event) {
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

  if (!nodeMesh || !filteredData) return;
  raycaster.setFromCamera(pointer, camera);
  const intersects = raycaster.intersectObject(nodeMesh);

  if (intersects.length > 0) {
    const idx = intersects[0].instanceId;
    const nodes = nodeMesh.userData.nodeList;
    if (idx < nodes.length) {
      hoveredNode = nodes[idx];
      showTooltip(hoveredNode, event.clientX, event.clientY);
      renderer.domElement.style.cursor = 'pointer';
      return;
    }
  }
  hoveredNode = null;
  hideTooltip();
  renderer.domElement.style.cursor = 'default';
}

function onPointerClick(event) {
  if (!hoveredNode) return;
  handleNodeClick(hoveredNode);
}

function handleNodeClick(node) {
  selectedNode = node;
  // 照搬：connectedIds = 节点 + 所有直接邻居
  const connectedIds = new Set([node.id]);
  for (const e of filteredData.edges) {
    if (e.source === node.id) connectedIds.add(e.target);
    if (e.target === node.id) connectedIds.add(e.source);
  }
  highlightedIds = connectedIds;
  // 相机飞行
  cameraTarget = computeCameraTarget(filteredData.nodes, connectedIds);
  cameraAnimProgress = 0;
  // 重渲染
  rebuildGraph();
  // 详情面板
  showDetailPanel(node);
}

// ════════════════════════════════════════════════════════
// 10. Tooltip（照搬 NodeTooltip — project camera → div）
// ════════════════════════════════════════════════════════

function showTooltip(node, screenX, screenY) {
  const tt = document.getElementById('tooltip');
  const color = node.color || colorForLabel(node.label);
  let html = `<div class="tt-name"><span style="color:${color}">●</span> ${node.name}</div>`;
  html += `<div class="tt-meta">${node.label}`;
  if (node.in_calls !== undefined) html += ` · ${node.in_calls} calls`;
  html += `</div>`;
  if (node.file_path) {
    const fp = node.file_path.replace(/\\/g, '/');
    const short = fp.split('/').slice(-2).join('/');
    html += `<div class="tt-meta">${short}:${node.start_line || '?'}</div>`;
  }
  html += `<div class="tt-meta" style="color:var(--primary)">click for details →</div>`;
  tt.innerHTML = html;
  tt.style.display = 'block';
  tt.style.left = (screenX + 14) + 'px';
  tt.style.top = (screenY + 14) + 'px';
}

function hideTooltip() {
  document.getElementById('tooltip').style.display = 'none';
}

function updateTooltipPosition() {
  if (!hoveredNode) return;
  // 保持 tooltip 跟随鼠标，不需要每帧更新（onPointerMove 已设位置）
}

// ════════════════════════════════════════════════════════
// 11. 详情面板（照搬 NodeDetailPanel — 连接前端算 + 源码懒加载）
// ════════════════════════════════════════════════════════

async function showDetailPanel(node) {
  const panel = document.getElementById('right-panel');
  const header = document.getElementById('detail-header');
  const body = document.getElementById('detail-body');
  const color = node.color || colorForLabel(node.label);

  let h = `<div class="badge" style="background:${color}30;color:${color}">${node.label}</div>`;
  h += `<h2>${node.name || '<anonymous>'}</h2>`;
  if (node.file_path) {
    h += `<div class="meta">${node.file_path}:${node.start_line || '?'}</div>`;
  }
  if (node.qualified_name && repoInfo?.blob_base && node.file_path) {
    const lineEnd = node.end_line && node.end_line !== node.start_line ? `-L${node.end_line}` : '';
    const url = `${repoInfo.blob_base}/${node.file_path.split('/').map(encodeURIComponent).join('/')}#L${node.start_line || 1}${lineEnd}`;
    h += `<a class="github-link" href="${url}" target="_blank">在 Git 中查看 →</a>`;
  }
  header.innerHTML = h;

  // 连接关系纯前端算（照搬 NodeDetailPanel connections）
  const nodeById = new Map(filteredData.nodes.map(n => [n.id, n]));
  const outbound = {}, inbound = {};
  for (const e of filteredData.edges) {
    if (e.source === node.id) {
      const tgt = nodeById.get(e.target);
      if (tgt) { (outbound[e.type] = outbound[e.type] || []).push(tgt); }
    }
    if (e.target === node.id) {
      const src = nodeById.get(e.source);
      if (src) { (inbound[e.type] = inbound[e.type] || []).push(src); }
    }
  }

  let b = '';
  // 出边
  b += '<div class="detail-section"><h4>出边 (Outbound)</h4>';
  let hasOut = false;
  for (const [type, items] of Object.entries(outbound)) {
    hasOut = true;
    const ec = EDGE_TYPE_COLORS[type] || DEFAULT_EDGE_COLOR;
    b += `<div class="conn-group-title" style="color:${ec}">${type} (${items.length})</div>`;
    for (const item of items.slice(0, 25)) {
      b += `<div class="conn-item" onclick="window._navNode(${item.id})">${node.name} <span class="arrow">→</span> ${item.name}</div>`;
    }
    if (items.length > 25) b += `<div style="font-size:11px;color:var(--fg-dim)">+${items.length - 25} more</div>`;
  }
  if (!hasOut) b += '<div style="font-size:11px;color:var(--fg-dim)">无</div>';
  b += '</div>';

  // 入边
  b += '<div class="detail-section"><h4>入边 (Inbound)</h4>';
  let hasIn = false;
  for (const [type, items] of Object.entries(inbound)) {
    hasIn = true;
    const ec = EDGE_TYPE_COLORS[type] || DEFAULT_EDGE_COLOR;
    b += `<div class="conn-group-title" style="color:${ec}">${type} (${items.length})</div>`;
    for (const item of items.slice(0, 25)) {
      b += `<div class="conn-item" onclick="window._navNode(${item.id})">${item.name} <span class="arrow">→</span> ${node.name}</div>`;
    }
    if (items.length > 25) b += `<div style="font-size:11px;color:var(--fg-dim)">+${items.length - 25} more</div>`;
  }
  if (!hasIn) b += '<div style="font-size:11px;color:var(--fg-dim)">无</div>';
  b += '</div>';

  // 源码按钮
  b += `<div class="detail-section"><h4>源码</h4><button onclick="window._loadSource(${node.id})">查看源码</button><div id="source-area"></div></div>`;

  body.innerHTML = b;
  panel.classList.add('active');
}

window._navNode = function(id) {
  const node = nodeMap.get(id);
  if (node) handleNodeClick(node);
};

window._loadSource = async function(id) {
  const area = document.getElementById('source-area');
  area.innerHTML = '<div style="font-size:11px;color:var(--fg-dim)">加载中...</div>';
  try {
    const res = await fetch(`/api/source/${id}?context=3`);
    const data = await res.json();
    if (data.error) { area.innerHTML = `<div style="color:var(--danger);font-size:11px">${data.error}</div>`; return; }
    const lines = data.source.split('\n');
    let html = `<div style="font-size:10px;color:var(--fg-dim);margin-bottom:4px">${data.file_path} L${data.node_start}-${data.node_end}</div>`;
    html += '<div class="source">';
    lines.forEach((line, i) => {
      const ln = data.start_line + i;
      const hl = ln >= data.node_start && ln <= data.node_end ? 'hl' : '';
      const esc = line.replace(/</g, '&lt;').replace(/>/g, '&gt;');
      html += `<div class="${hl}">${String(ln).padStart(4)}: ${esc}</div>`;
    });
    html += '</div>';
    area.innerHTML = html;
  } catch (e) {
    area.innerHTML = `<div style="color:var(--danger);font-size:11px">${e}</div>`;
  }
};

// ════════════════════════════════════════════════════════
// 12. 文件树（照搬 Sidebar.tsx buildFileTree + flattenSingleChild）
// ════════════════════════════════════════════════════════

function buildFileTree(nodes) {
  const root = { children: new Map(), directNodes: [], nodeIds: new Set() };
  for (const n of nodes) {
    if (!n.file_path) continue;
    const parts = n.file_path.replace(/\\/g, '/').split('/');
    let cur = root;
    for (let i = 0; i < parts.length; i++) {
      const p = parts[i];
      if (i === parts.length - 1) {
        cur.directNodes.push(n);
        cur.nodeIds.add(n.id);
      } else {
        if (!cur.children.has(p)) {
          cur.children.set(p, { children: new Map(), directNodes: [], nodeIds: new Set(), name: p });
        }
        cur = cur.children.get(p);
        cur.nodeIds.add(n.id);
      }
    }
  }
  flattenSingleChild(root);
  return root;
}

function flattenSingleChild(node) {
  for (const [key, child] of node.children) {
    flattenSingleChild(child);
    if (child.children.size === 1 && child.directNodes.length === 0) {
      const [grandKey, grandChild] = child.children.entries().next().value;
      const merged = { ...grandChild, name: key + '/' + grandChild.name };
      node.children.set(key, merged);
    }
  }
}

function renderFileTree(tree) {
  const container = document.getElementById('file-tree');
  container.innerHTML = '';
  renderTreeItem(container, tree, '', 0);
}

function renderTreeItem(container, node, path, depth) {
  for (const [key, child] of node.children) {
    const item = document.createElement('div');
    item.className = 'tree-item';
    const fullPath = path ? path + '/' + key : key;
    const indent = '  '.repeat(depth);
    item.innerHTML = `${indent}${key} <span class="count">(${child.nodeIds.size})</span>`;
    item.onclick = (e) => {
      e.stopPropagation();
      // 高亮该目录所有节点（照搬 handleSelectPath）
      highlightedIds = new Set(child.nodeIds);
      cameraTarget = computeCameraTarget(filteredData.nodes, highlightedIds);
      cameraAnimProgress = 0;
      rebuildGraph();
      document.querySelectorAll('.tree-item').forEach(el => el.classList.remove('selected'));
      item.classList.add('selected');
    };
    container.appendChild(item);
    renderTreeItem(container, child, fullPath, depth + 1);
  }
}

// ════════════════════════════════════════════════════════
// 13. 过滤面板（照搬 FilterPanel.tsx）
// ════════════════════════════════════════════════════════

function renderFilters() {
  if (!graphData) return;
  // 节点类型
  const labelCounts = {};
  for (const n of graphData.nodes) labelCounts[n.label] = (labelCounts[n.label] || 0) + 1;
  const kindDiv = document.getElementById('kind-filters');
  kindDiv.innerHTML = '';
  for (const [label, count] of Object.entries(labelCounts).sort((a, b) => b[1] - a[1])) {
    const color = colorForLabel(label);
    const item = document.createElement('div');
    item.className = 'filter-item';
    item.innerHTML = `<input type="checkbox" checked data-label="${label}"><span class="dot" style="background:${color}"></span>${label}<span class="count">${count}</span>`;
    item.querySelector('input').onchange = () => { updateFilters(); };
    kindDiv.appendChild(item);
  }

  // 边类型
  const edgeCounts = {};
  for (const e of graphData.edges) edgeCounts[e.type] = (edgeCounts[e.type] || 0) + 1;
  const edgeDiv = document.getElementById('edge-filters');
  edgeDiv.innerHTML = '';
  for (const [type, count] of Object.entries(edgeCounts).sort((a, b) => b[1] - a[1])) {
    const color = EDGE_TYPE_COLORS[type] || DEFAULT_EDGE_COLOR;
    const item = document.createElement('div');
    item.className = 'filter-item';
    const display = type.replace(/_/g, ' ').toLowerCase();
    item.innerHTML = `<input type="checkbox" checked data-type="${type}"><span class="dot" style="background:${color}"></span>${display}<span class="count">${count}</span>`;
    item.querySelector('input').onchange = () => { updateFilters(); };
    edgeDiv.appendChild(item);
  }
}

function updateFilters() {
  enabledLabels.clear();
  document.querySelectorAll('#kind-filters input').forEach(cb => {
    if (cb.checked) enabledLabels.add(cb.dataset.label);
  });
  enabledEdgeTypes.clear();
  document.querySelectorAll('#edge-filters input').forEach(cb => {
    if (cb.checked) enabledEdgeTypes.add(cb.dataset.type);
  });
  applyFilters();
}

function selectAllKinds(selectAll) {
  document.querySelectorAll('#kind-filters input').forEach(cb => { cb.checked = selectAll; });
  updateFilters();
}

window.selectAllKinds = selectAllKinds;

// ════════════════════════════════════════════════════════
// 14. 图控制器（照搬 GraphTab — filter + rebuild + budget）
// ════════════════════════════════════════════════════════

function applyFilters() {
  if (!graphData) return;
  // 照搬 filteredData memo
  const keepNode = n => enabledLabels.has(n.label);
  const filteredNodes = graphData.nodes.filter(keepNode);
  const nodeIdSet = new Set(filteredNodes.map(n => n.id));
  const filteredEdges = graphData.edges.filter(e =>
    enabledEdgeTypes.has(e.type) && nodeIdSet.has(e.source) && nodeIdSet.has(e.target)
  );
  filteredData = { nodes: filteredNodes, edges: filteredEdges };
  nodeMap = new Map(filteredNodes.map(n => [n.id, n]));
  rebuildGraph();
  renderFileTree(buildFileTree(filteredNodes));
  updateHUD();
}

function rebuildGraph() {
  if (!filteredData) return;
  buildNodeCloud(filteredData.nodes);
  buildEdgeLines(filteredData.nodes, filteredData.edges);
  buildLabels(filteredData.nodes);
}

function updateHUD() {
  const hud = document.getElementById('hud');
  if (!filteredData || !graphData) return;
  hud.textContent = `${filteredData.nodes.length} / ${graphData.total_nodes} nodes · ${filteredData.edges.length} edges`;
  if (graphData.total_nodes > filteredData.nodes.length) {
    hud.textContent += ` (increase budget for more)`;
  }
}

// ════════════════════════════════════════════════════════
// 15. 数据获取（照搬 useGraphData.fetchLayout）
// ════════════════════════════════════════════════════════

async function fetchGraph() {
  const loading = document.getElementById('loading');
  loading.style.display = 'block';
  const budget = parseInt(document.getElementById('budget-input').value) || 5000;

  try {
    const [layoutRes, repoRes] = await Promise.all([
      fetch(`/api/layout?max_nodes=${budget}`),
      fetch('/api/repo-info'),
    ]);
    graphData = await layoutRes.json();
    repoInfo = await repoRes.json();
    // 初始化 filter（全选）
    enabledLabels = new Set(graphData.nodes.map(n => n.label));
    enabledEdgeTypes = new Set(graphData.edges.map(e => e.type));
    renderFilters();
    applyFilters();
  } catch (e) {
    document.getElementById('hud').textContent = `Error: ${e}`;
  } finally {
    loading.style.display = 'none';
  }
}

window.fetchGraph = fetchGraph;

// ════════════════════════════════════════════════════════
// 16. 显示设置（照搬 DisplaySettingsMenu）
// ════════════════════════════════════════════════════════

function toggleDisplayMenu() {
  document.getElementById('display-menu').classList.toggle('active');
}
window.toggleDisplayMenu = toggleDisplayMenu;

function setupDisplaySliders() {
  const sliders = [
    ['slider-edge', 'val-edge', 'edgeBrightness'],
    ['slider-glow', 'val-glow', 'nodeGlow'],
    ['slider-bloom', 'val-bloom', 'bloom'],
  ];
  for (const [sliderId, valId, key] of sliders) {
    const slider = document.getElementById(sliderId);
    const val = document.getElementById(valId);
    slider.oninput = () => {
      display[key] = parseFloat(slider.value);
      val.textContent = display[key].toFixed(2);
      rebuildGraph();
    };
  }
}

// ════════════════════════════════════════════════════════
// 17. 搜索
// ════════════════════════════════════════════════════════

function setupSearch() {
  const input = document.getElementById('search-input');
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
  });
}

// ════════════════════════════════════════════════════════
// 18. 窗口 resize
// ════════════════════════════════════════════════════════

function onResize() {
  const container = document.getElementById('canvas-container');
  const w = container.clientWidth, h = container.clientHeight;
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  renderer.setSize(w, h);
}

// ════════════════════════════════════════════════════════
// 19. 启动
// ════════════════════════════════════════════════════════

window.addEventListener('DOMContentLoaded', () => {
  initScene();
  setupDisplaySliders();
  setupSearch();
  fetchGraph();
});
