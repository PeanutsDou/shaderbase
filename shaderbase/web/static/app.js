// shaderbase 知识图谱 — 2D Canvas 力向图
// 照搬 konwleage map/tools/template.html 的渲染风格：
// - Canvas 2D 实时力导向（斥力 + 弹簧 + 中心引力 + 阻尼）
// - 节点小圆点（type-dot 风格，按 kind 上色）
// - 边细线（关联高亮时加粗）
// - 滚轮缩放 + 拖拽平移 + 拖节点
// 在此基础上扩展：左栏过滤/文件树、右栏详情面板、dead-code 视图

// ════════════════════════════════════════════════════════
// §1 颜色映射（与服务端 layout.py KIND_COLORS 一致）
// ════════════════════════════════════════════════════════

const LABEL_COLORS = {
  Function:     '#58a6ff',
  Struct:       '#3fb950',
  Texture:      '#d29922',
  SamplerState: '#f85149',
  Uniform:      '#bc8cff',
  Technique:    '#f97583',
  CBuffer:      '#c9d1d9',
};
const DEFAULT_LABEL_COLOR = '#8b949e';
function colorForLabel(label) { return LABEL_COLORS[label] || DEFAULT_LABEL_COLOR; }

const STATUS_COLORS = {
  dead: '#ef4444', single: '#f97316', entry: '#3b82f6', test: '#a855f7',
  normal: '#22c55e', exported: '#475569', structural: '#334155',
};
const STATUS_DEFAULT = '#334155';
function colorForStatus(s) { return s ? (STATUS_COLORS[s] || STATUS_DEFAULT) : STATUS_DEFAULT; }

const STATUS_LEGEND = [
  { status: 'dead', label: 'Dead (0 callers)', color: STATUS_COLORS.dead },
  { status: 'single', label: 'One caller', color: STATUS_COLORS.single },
  { status: 'entry', label: 'Entry / route', color: STATUS_COLORS.entry },
  { status: 'test', label: 'Test', color: STATUS_COLORS.test },
  { status: 'normal', label: 'Normal', color: STATUS_COLORS.normal },
];

const EDGE_TYPE_COLORS = {
  CALLS: '#1f6feb',
  INCLUDES: '#a371f7',
  HAS_MEMBER: '#3fb950',
  IS_ENTRY_POINT: '#f97583',
  EXPOSES_TECHNIQUE: '#f97583',
};
const DEFAULT_EDGE_COLOR = '#30363d';

// ════════════════════════════════════════════════════════
// §2 node budget（持久化）
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

// ════════════════════════════════════════════════════════
// §3 全局状态
// ════════════════════════════════════════════════════════

let currentProject = 'g66';
let graphData = null;        // 原始 /api/layout
let filteredData = null;     // 应用 filter 后
let activeNodeId = null;     // 当前选中节点 id
let hoveredNode = null;
let repoInfo = null;

let enabledLabels = new Set();
let enabledEdgeTypes = new Set();
let showLabels = false;      // 默认不显示标签（图清晰后再开）
let deadCodeView = false;
let showOnlyDead = false;
let hideEntryPoints = false;
let hideTests = false;

// Canvas 力向图状态
let canvas, ctx, W = 0, H = 0, dpr = 1;
let viewX = 0, viewY = 0, viewScale = 1;
let simNodes = [], simEdges = [], simMap = {};
let simWarm = 1;
let isPan = false, dragN = null, lx = 0, ly = 0;
let lastInteraction = Date.now();

// ════════════════════════════════════════════════════════
// §4 Canvas 初始化 + 力导向（1:1 照搬 template.html）
// ════════════════════════════════════════════════════════

function initCanvas() {
  canvas = document.getElementById('graph-canvas');
  ctx = canvas.getContext('2d');
  dpr = window.devicePixelRatio || 1;
  resize();
  window.addEventListener('resize', resize);

  canvas.addEventListener('mousedown', onCanvasMouseDown);
  canvas.addEventListener('mousemove', onCanvasMouseMove);
  canvas.addEventListener('mouseup', onCanvasMouseUp);
  canvas.addEventListener('mouseleave', onCanvasMouseUp);
  canvas.addEventListener('click', onCanvasClick);
  canvas.addEventListener('wheel', onCanvasWheel, { passive: false });

  requestAnimationFrame(loop);
}

function resize() {
  if (!canvas) return;
  const r = canvas.getBoundingClientRect();
  W = r.width; H = r.height;
  canvas.width = W * dpr;
  canvas.height = H * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function getR(n) {
  // 照搬 template.html getR：5–16，按 edge_count
  return Math.max(5, Math.min(16, 3 + (n.edge_count || n.in_calls || 0) * 1.5));
}

function initSim() {
  if (!filteredData) return;
  simNodes = filteredData.nodes.map(n => ({
    id: n.id,
    name: n.name,
    label: n.label,
    color: deadCodeView ? colorForStatus(n.status) : (n.color || colorForLabel(n.label)),
    edgeCount: n.edge_count || 0,
    in_calls: n.in_calls || 0,
    status: n.status,
    radius: getR(n),
    x: W / 2 + (Math.random() - 0.5) * Math.min(W, 1200),
    y: H / 2 + (Math.random() - 0.5) * Math.min(H, 800),
    vx: 0, vy: 0,
  }));
  simMap = {};
  simNodes.forEach(n => { simMap[n.id] = n; });
  simEdges = filteredData.edges
    .filter(e => simMap[e.source] && simMap[e.target])
    .map(e => ({ source: simMap[e.source], target: simMap[e.target], type: e.type }));
  simWarm = 1;
  // 初始定位到画布中心 + 默认缩放
  viewX = 0; viewY = 0; viewScale = 1;
}

// 每 N 帧自动 fit 一次（前 ~3 秒力向还没稳住时持续拉回）
let _fitCounter = 0;
function maybeAutoFit() {
  if (simNodes.length === 0) return;
  _fitCounter++;
  // 前 180 帧（约 3 秒 @60fps）每 30 帧拉一次，之后不再自动调
  if (_fitCounter <= 180 && _fitCounter % 30 === 0) autoFit();
}

function step() {
  if (simNodes.length === 0) return;
  const n = simNodes.length;
  simWarm *= 0.997;
  if (simWarm < 0.01) simWarm = 0.01;

  // 力参数随节点数自适应缩放：大图斥力弱、阻尼强，避免发散飞出视口
  // （template.html 的固定参数只适合 <500 节点，5000 节点会瞬间发散）
  const density = Math.sqrt(n);          // 节点密度估计
  const REP = 800 / density;             // 斥力随密度衰减
  const LINK = 120 / Math.sqrt(density); // 弹簧目标距离缩短
  const LK = 0.025, CK = 0.0015, DAMP = 0.82;
  const forceScale = simWarm > 0.05 ? simWarm : 0.05;
  const MAX_SPEED = 8;                   // 单帧位移上限（照搬 layout3d.c）

  // 节点质量：基于 edgeCount，范围 1~3
  if (!simNodes[0]._mass) {
    for (let i = 0; i < n; i++) {
      simNodes[i]._mass = Math.min(3, 1 + (simNodes[i].edgeCount || 0) / 30);
    }
  }

  // 斥力（O(n²) — 大图采样优化）
  const step = Math.max(1, Math.floor(n / 500));
  for (let i = 0; i < n; i++) {
    const a = simNodes[i];
    for (let j = i + 1; j < n; j += step) {
      const b = simNodes[j];
      let dx = b.x - a.x, dy = b.y - a.y;
      let d2 = dx * dx + dy * dy;
      if (d2 < 100) d2 = 100;
      const dist = Math.sqrt(d2);
      const f = REP / d2 * forceScale;
      const totalMass = a._mass + b._mass;
      a.vx -= dx / dist * f * (b._mass / totalMass);
      a.vy -= dy / dist * f * (b._mass / totalMass);
      b.vx += dx / dist * f * (a._mass / totalMass);
      b.vy += dy / dist * f * (a._mass / totalMass);
    }
  }
  // 弹簧力
  for (let i = 0; i < simEdges.length; i++) {
    const e = simEdges[i];
    const dx = e.target.x - e.source.x, dy = e.target.y - e.source.y;
    const dist = Math.sqrt(dx * dx + dy * dy) || 1;
    const f = (dist - LINK) * LK * forceScale;
    e.source.vx += dx / dist * f;
    e.source.vy += dy / dist * f;
    e.target.vx -= dx / dist * f;
    e.target.vy -= dy / dist * f;
  }
  // 中心引力
  const cx = W / 2, cy = H / 2;
  for (let i = 0; i < n; i++) {
    const s = simNodes[i];
    const m = 1 / s._mass;
    s.vx += (cx - s.x) * CK * forceScale * m;
    s.vy += (cy - s.y) * CK * forceScale * m;
    s.vx *= DAMP;
    s.vy *= DAMP;
    // 单帧位移上限：力很大时按比例缩，避免发散飞出视口
    const fm = Math.sqrt(s.vx * s.vx + s.vy * s.vy);
    let speed = 1;
    if (speed * fm > MAX_SPEED) speed = MAX_SPEED / (fm + 0.001);
    s.x += s.vx * speed;
    s.y += s.vy * speed;
  }
}

// 自动 fit-to-view：算所有节点 bounding box，调 viewScale/viewX/viewY 让图填满画布
function autoFit() {
  if (simNodes.length === 0) return;
  let mnx = Infinity, mny = Infinity, mxx = -Infinity, mxy = -Infinity;
  for (const s of simNodes) {
    if (s.x < mnx) mnx = s.x;
    if (s.y < mny) mny = s.y;
    if (s.x > mxx) mxx = s.x;
    if (s.y > mxy) mxy = s.y;
  }
  const bw = Math.max(1, mxx - mnx);
  const bh = Math.max(1, mxy - mny);
  // 留 10% 边距
  const sx = (W * 0.9) / bw;
  const sy = (H * 0.9) / bh;
  viewScale = Math.max(0.05, Math.min(4, Math.min(sx, sy)));
  const ccx = (mnx + mxx) / 2;
  const ccy = (mny + mxy) / 2;
  viewX = W / 2 - ccx * viewScale;
  viewY = H / 2 - ccy * viewScale;
  heat();
}

function draw() {
  if (!ctx) return;
  ctx.clearRect(0, 0, W, H);
  ctx.save();
  ctx.translate(viewX, viewY);
  ctx.scale(viewScale, viewScale);

  // 高亮集
  let hl = null;
  if (activeNodeId) {
    hl = {};
    hl[activeNodeId] = 1;
    for (let i = 0; i < simEdges.length; i++) {
      const e = simEdges[i];
      if (e.source.id === activeNodeId) hl[e.target.id] = 1;
      if (e.target.id === activeNodeId) hl[e.source.id] = 1;
    }
  } else if (hoveredNode) {
    hl = {};
    hl[hoveredNode.id] = 1;
    for (let i = 0; i < simEdges.length; i++) {
      const e = simEdges[i];
      if (e.source.id === hoveredNode.id) hl[e.target.id] = 1;
      if (e.target.id === hoveredNode.id) hl[e.source.id] = 1;
    }
  }

  // 边（非高亮的先画细线）
  ctx.lineWidth = 0.6 / viewScale;
  for (let i = 0; i < simEdges.length; i++) {
    const e = simEdges[i];
    const isHL = hl && (e.source.id === activeNodeId || e.target.id === activeNodeId
      || e.source.id === (hoveredNode && hoveredNode.id) || e.target.id === (hoveredNode && hoveredNode.id));
    if (isHL) continue;
    const ec = EDGE_TYPE_COLORS[e.type] || DEFAULT_EDGE_COLOR;
    ctx.strokeStyle = hl ? 'rgba(48,54,61,0.2)' : ec + '88';
    ctx.beginPath();
    ctx.moveTo(e.source.x, e.source.y);
    ctx.lineTo(e.target.x, e.target.y);
    ctx.stroke();
  }
  // 高亮边加粗
  if (hl) {
    ctx.lineWidth = 1.5 / viewScale;
    for (let i = 0; i < simEdges.length; i++) {
      const e = simEdges[i];
      const isHL = (e.source.id === activeNodeId || e.target.id === activeNodeId
        || e.source.id === (hoveredNode && hoveredNode.id) || e.target.id === (hoveredNode && hoveredNode.id));
      if (!isHL) continue;
      const ec = EDGE_TYPE_COLORS[e.type] || DEFAULT_EDGE_COLOR;
      ctx.strokeStyle = ec;
      ctx.beginPath();
      ctx.moveTo(e.source.x, e.source.y);
      ctx.lineTo(e.target.x, e.target.y);
      ctx.stroke();
    }
  }

  // 节点
  for (let i = 0; i < simNodes.length; i++) {
    const s = simNodes[i];
    const isA = s.id === activeNodeId;
    const isHover = s.id === (hoveredNode && hoveredNode.id);
    const dim = hl && !hl[s.id];
    if (dim) ctx.globalAlpha = 0.25;
    if (isA || isHover) {
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.radius + 4, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(88,166,255,0.25)';
      ctx.fill();
    }
    ctx.beginPath();
    ctx.arc(s.x, s.y, s.radius, 0, Math.PI * 2);
    ctx.fillStyle = s.color;
    ctx.fill();
    ctx.strokeStyle = isA ? '#f0f6fc' : (isHover ? '#c9d1d9' : '#21262d');
    ctx.lineWidth = (isA ? 2 : (isHover ? 1.5 : 1)) / viewScale;
    ctx.stroke();
    // 文字
    if (showLabels || isA || isHover) {
      ctx.fillStyle = isA ? '#f0f6fc' : (isHover ? '#c9d1d9' : '#8b949e');
      ctx.font = (isA ? 'bold 11px ' : '10px ') + '"Segoe UI","Microsoft YaHei",sans-serif';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      const name = (s.name || '?').substring(0, 20);
      ctx.fillText(name, s.x + s.radius + 3, s.y);
    }
    ctx.globalAlpha = 1;
  }
  ctx.restore();
}

function loop() {
  step();
  draw();
  maybeAutoFit();
  requestAnimationFrame(loop);
}

function toWorld(sx, sy) { return { x: (sx - viewX) / viewScale, y: (sy - viewY) / viewScale }; }
function pickNode(wx, wy) {
  for (let i = simNodes.length - 1; i >= 0; i--) {
    const n = simNodes[i];
    const r = n.radius + 3;
    if ((wx - n.x) * (wx - n.x) + (wy - n.y) * (wy - n.y) <= r * r) return n;
  }
  return null;
}
function heat() { simWarm = Math.max(simWarm, 0.15); lastInteraction = Date.now(); }

function onCanvasMouseDown(e) {
  const p = toWorld(e.offsetX, e.offsetY);
  const hit = pickNode(p.x, p.y);
  if (hit) { dragN = hit; hit.vx = 0; hit.vy = 0; heat(); }
  else { isPan = true; }
  lx = e.offsetX; ly = e.offsetY;
}
function onCanvasMouseMove(e) {
  if (dragN) {
    const p = toWorld(e.offsetX, e.offsetY);
    dragN.x = p.x; dragN.y = p.y;
    heat();
  } else if (isPan) {
    viewX += e.offsetX - lx; viewY += e.offsetY - ly;
    lx = e.offsetX; ly = e.offsetY;
    heat();
  } else {
    const p = toWorld(e.offsetX, e.offsetY);
    const hit = pickNode(p.x, p.y);
    if (hit !== hoveredNode) {
      hoveredNode = hit;
      canvas.style.cursor = hit ? 'pointer' : 'grab';
      if (hit) showTooltip(hit, e.clientX, e.clientY);
      else hideTooltip();
    } else if (hit) {
      // tooltip 跟随
      showTooltip(hit, e.clientX, e.clientY);
    }
  }
}
function onCanvasMouseUp() { isPan = false; dragN = null; }
function onCanvasClick(e) {
  const p = toWorld(e.offsetX, e.offsetY);
  const hit = pickNode(p.x, p.y);
  if (hit) selectNode(hit.id);
  else { activeNodeId = null; updateClearButton(); hideTooltip(); }
}
function onCanvasWheel(e) {
  e.preventDefault();
  const f = e.deltaY > 0 ? 0.9 : 1.1;
  const ns = Math.max(0.15, Math.min(4, viewScale * f));
  viewX = e.offsetX - (e.offsetX - viewX) * (ns / viewScale);
  viewY = e.offsetY - (e.offsetY - viewY) * (ns / viewScale);
  viewScale = ns;
  heat();
}

// ════════════════════════════════════════════════════════
// §5 节点选择 + 详情面板（对齐 NodeDetailPanel + template.html selectNode）
// ════════════════════════════════════════════════════════

function selectNode(nodeId) {
  activeNodeId = nodeId;
  heat();
  updateClearButton();
  const n = simMap[nodeId] || (filteredData && filteredData.nodes.find(x => x.id === nodeId));
  if (!n) return;
  showDetailPanel(n);
}

function lineSuffix(node) {
  if (!node.start_line) return '';
  const end = (node.end_line && node.end_line !== node.start_line) ? '-L' + node.end_line : '';
  return '#L' + node.start_line + end;
}
function encodePath(p) { return p.split('/').map(encodeURIComponent).join('/'); }
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
  const ghUrl = githubUrl(node);
  h += '<div class="dh-actions">';
  h += '<button id="btn-show-code">查看源码</button>';
  if (ghUrl) h += '<a class="dh-git" href="' + ghUrl + '" target="_blank" rel="noopener">在 Git 中查看 ↗</a>';
  h += '</div>';
  h += '<div id="source-area"></div>';
  header.innerHTML = h;

  // 连接关系
  const nodeById = new Map(filteredData.nodes.map(n => [n.id, n]));
  const outbound = {}, inbound = {};
  for (const e of filteredData.edges) {
    if (e.source === node.id) {
      const t = nodeById.get(e.target);
      if (t) (outbound[e.type] = outbound[e.type] || []).push(t);
    }
    if (e.target === node.id) {
      const s = nodeById.get(e.source);
      if (s) (inbound[e.type] = inbound[e.type] || []).push(s);
    }
  }
  const outCount = Object.values(outbound).reduce((a, b) => a + b.length, 0);
  const inCount = Object.values(inbound).reduce((a, b) => a + b.length, 0);

  let b = '<div class="dh-stats">';
  b += '<div class="dh-stat"><span class="dh-stat-label">出边</span><span class="dh-stat-val out">' + outCount + '</span></div>';
  b += '<div class="dh-stat"><span class="dh-stat-label">入边</span><span class="dh-stat-val in">' + inCount + '</span></div>';
  b += '<div class="dh-stat"><span class="dh-stat-label">总数</span><span class="dh-stat-val">' + (outCount + inCount) + '</span></div>';
  b += '</div><div class="detail-body-inner">';
  b += renderConnSection('引用 (Outbound)', '→', outbound);
  b += renderConnSection('被引用 (Inbound)', '←', inbound);
  b += '</div>';
  body.innerHTML = b;
  panel.classList.add('active');

  document.getElementById('btn-close-detail').onclick = clearSelection;
  document.getElementById('btn-show-code').onclick = () => loadSource(node.id);
  body.querySelectorAll('.conn-item').forEach(el => {
    el.onclick = () => {
      const id = parseInt(el.dataset.nodeId, 10);
      const n2 = nodeById.get(id);
      if (n2) selectNode(id);
    };
  });
}

function renderConnSection(title, icon, grouped) {
  const total = Object.values(grouped).reduce((a, b) => a + b.length, 0);
  let h = '<div class="detail-section"><p class="ds-title">' + title;
  h += ' <span class="ds-count">(' + total + ')</span></p>';
  if (total === 0) { h += '<p class="ds-empty">无</p></div>'; return h; }
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
        + '<span class="conn-label">' + escapeHtml(item.label) + '</span></div>';
    }
    if (items.length > 25) h += '<p class="ds-more">+' + (items.length - 25) + ' more</p>';
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
    if (data.error) { area.innerHTML = '<div class="src-error">' + escapeHtml(data.error) + '</div>'; return; }
    const lines = data.source.split('\n');
    let html = '<div class="src-meta">' + escapeHtml(data.file_path) + ' L' + data.node_start + '-' + data.node_end + '</div><div class="source">';
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
  activeNodeId = null;
  hideTooltip();
  document.getElementById('right-panel').classList.remove('active');
  updateClearButton();
}

// ════════════════════════════════════════════════════════
// §6 Tooltip（照搬 template.html 简化版）
// ════════════════════════════════════════════════════════

function showTooltip(node, sx, sy) {
  const tt = document.getElementById('tooltip');
  const c = deadCodeView ? colorForStatus(node.status) : (node.color || colorForLabel(node.label));
  let html = '<div class="tt-name"><span class="tt-dot" style="background:' + c + '"></span>'
    + '<span class="tt-name-text">' + escapeHtml(node.name) + '</span>'
    + '<span class="tt-label">' + escapeHtml(node.label) + '</span></div>';
  if (node.in_calls !== undefined) {
    html += '<div class="tt-meta">' + node.in_calls + ' caller' + (node.in_calls === 1 ? '' : 's')
      + (node.status && node.status !== 'structural' ? ' · ' + node.status : '') + '</div>';
  }
  html += '<div class="tt-hint">点击查看详情 →</div>';
  tt.innerHTML = html;
  tt.style.display = 'block';
  tt.style.left = (sx + 14) + 'px';
  tt.style.top = (sy + 14) + 'px';
}
function hideTooltip() { const tt = document.getElementById('tooltip'); if (tt) tt.style.display = 'none'; }

function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ════════════════════════════════════════════════════════
// §7 过滤面板（照搬 FilterPanel.tsx 简化版）
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
    legend.innerHTML = STATUS_LEGEND.map(s => '<span class="legend-item"><span class="legend-dot" style="background:' + s.color + '"></span>' + escapeHtml(s.label) + '</span>').join('');
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
// §8 图控制器（filter + initSim + HUD）
// ════════════════════════════════════════════════════════

function applyFilters() {
  if (!graphData) return;
  const statusOk = (n) => {
    if (showOnlyDead && n.status !== 'dead') return false;
    if (hideEntryPoints && n.status === 'entry') return false;
    if (hideTests && n.status === 'test') return false;
    return true;
  };
  const keep = (n) => enabledLabels.has(n.label) && statusOk(n);
  const filteredNodes = graphData.nodes.filter(keep);
  const nodeIdSet = new Set(filteredNodes.map(n => n.id));
  const filteredEdges = graphData.edges.filter(e =>
    enabledEdgeTypes.has(e.type) && nodeIdSet.has(e.source) && nodeIdSet.has(e.target)
  );
  filteredData = { nodes: filteredNodes, edges: filteredEdges };

  initSim();
  renderFileTree(filteredNodes);
  renderFilters();
  updateHUD();
  updateClearButton();
}

function updateHUD() {
  if (!filteredData || !graphData) return;
  const main = document.getElementById('hud-main');
  const filt = document.getElementById('hud-filtered');
  const notice = document.getElementById('hud-notice');
  const sel = document.getElementById('hud-selected');
  if (main) main.textContent = filteredData.nodes.length + ' nodes / ' + filteredData.edges.length + ' edges';
  if (filt) filt.textContent = (graphData.nodes.length > filteredData.nodes.length)
    ? 'filtered from ' + graphData.nodes.length : '';
  if (notice) notice.textContent = (graphData.total_nodes > graphData.nodes.length)
    ? 'Showing ' + graphData.nodes.length + ' of ' + graphData.total_nodes + ' nodes — raise budget for more' : '';
  if (sel) sel.textContent = activeNodeId ? '1 selected' : '';
}

function updateClearButton() {
  const btn = document.getElementById('btn-clear-sel');
  const wrap = document.getElementById('clear-sel-wrap');
  const show = !!activeNodeId;
  if (btn) btn.style.display = show ? 'inline-block' : 'none';
  if (wrap) wrap.style.display = show ? 'block' : 'none';
}

// ════════════════════════════════════════════════════════
// §9 文件树（照搬 Sidebar.tsx buildFileTree + flattenSingleChild）
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
    const filtered = nodes.filter(n => (n.name || '').toLowerCase().includes(search) || (n.file_path || '').toLowerCase().includes(search)).slice(0, 50);
    if (filtered.length === 0) { container.innerHTML = '<p class="ft-empty">无匹配</p>'; return; }
    for (const n of filtered) {
      const el = document.createElement('button');
      el.className = 'ft-leaf';
      el.innerHTML = '<span class="ft-dot" style="background:' + (deadCodeView ? colorForStatus(n.status) : (n.color || colorForLabel(n.label))) + '"></span>'
        + '<span class="ft-name">' + escapeHtml(n.name) + '</span>'
        + '<span class="ft-path">' + escapeHtml(n.file_path || '') + '</span>';
      el.onclick = () => selectNode(n.id);
      container.appendChild(el);
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
        leaf.innerHTML = '<span class="ft-dot" style="background:' + (deadCodeView ? colorForStatus(gn.status) : (gn.color || colorForLabel(gn.label))) + '"></span>'
          + '<span class="ft-leaf-name">' + escapeHtml(gn.name) + '</span>'
          + '<span class="ft-leaf-label">' + escapeHtml(gn.label) + '</span>';
        leaf.onclick = (ev) => { ev.stopPropagation(); selectNode(gn.id); };
        childContainer.appendChild(leaf);
      }
    } else {
      childContainer.innerHTML = '';
    }
    // 高亮该目录所有节点 → 设置为 hover 高亮集
    if (dir.nodeIds.size > 0) selectNode([...dir.nodeIds][0]);
  };
  container.appendChild(item);
  container.appendChild(childContainer);
}

// ════════════════════════════════════════════════════════
// §10 数据获取
// ════════════════════════════════════════════════════════

async function fetchGraph() {
  const loading = document.getElementById('loading');
  if (loading) loading.style.display = 'flex';
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
    const err = document.getElementById('load-error');
    if (err) { err.textContent = e.message; err.style.display = 'block'; }
    const hud = document.getElementById('hud-main');
    if (hud) hud.textContent = 'Error: ' + e.message;
  } finally {
    if (loading) loading.style.display = 'none';
  }
}

// ════════════════════════════════════════════════════════
// §11 开关/搜索/重置
// ════════════════════════════════════════════════════════

function setupToggles() {
  const toggles = [
    ['toggle-dead-view', 'cr-dead-view', v => deadCodeView = v],
    ['toggle-only-dead', 'cr-only-dead', v => showOnlyDead = v],
    ['toggle-hide-entry', 'cr-hide-entry', v => hideEntryPoints = v],
    ['toggle-hide-tests', 'cr-hide-tests', v => hideTests = v],
  ];
  for (const [id, rowId, setter] of toggles) {
    const el = document.getElementById(id);
    const row = document.getElementById(rowId);
    if (!el || !row) continue;
    el.checked = false;
    row.classList.toggle('on', false);
    row.addEventListener('click', (e) => {
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
    slEl.checked = showLabels;
    slRow.classList.toggle('on', showLabels);
    slRow.addEventListener('click', (e) => {
      if (e.target === slEl) return;
      slEl.checked = !slEl.checked;
      slEl.dispatchEvent(new Event('change'));
    });
    slEl.onchange = () => {
      showLabels = slEl.checked;
      slRow.classList.toggle('on', showLabels);
    };
  }
}

function setupSearch() {
  const input = document.getElementById('search-input');
  if (!input) return;
  input.addEventListener('input', () => { if (filteredData) renderFileTree(filteredData.nodes); });
  input.addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    const q = input.value.trim().toLowerCase();
    if (!q || !filteredData) return;
    const matches = filteredData.nodes.filter(n => (n.name || '').toLowerCase().includes(q));
    if (matches.length === 0) return;
    selectNode(matches[0].id);
  });
}

function setupGraphControls() {
  const fitBtn = document.getElementById('btn-fit');
  if (fitBtn) {
    fitBtn.addEventListener('click', () => { autoFit(); });
  }
  const labelBtn = document.getElementById('toggle-labels');
  if (labelBtn) {
    labelBtn.addEventListener('click', () => {
      showLabels = !showLabels;
      labelBtn.classList.toggle('active', showLabels);
      labelBtn.textContent = showLabels ? '文字 ✓' : '文字';
      const slEl = document.getElementById('toggle-show-labels');
      const slRow = document.getElementById('cr-show-labels');
      if (slEl) slEl.checked = showLabels;
      if (slRow) slRow.classList.toggle('on', showLabels);
    });
  }
  const btnAll = document.getElementById('btn-all');
  if (btnAll) {
    btnAll.addEventListener('click', () => {
      activeNodeId = null;
      hideTooltip();
      updateClearButton();
      document.getElementById('right-panel').classList.remove('active');
      heat();
    });
  }
}

// ════════════════════════════════════════════════════════
// 启动
// ════════════════════════════════════════════════════════

window.addEventListener('DOMContentLoaded', () => {
  initCanvas();
  setupToggles();
  setupSearch();
  setupGraphControls();

  const persisted = loadNodeBudget(currentProject);
  document.getElementById('budget-input').value = persisted;

  fetchGraph();
});

window.fetchGraph = fetchGraph;
window.enableAllFilters = enableAllFilters;
window.disableAllFilters = disableAllFilters;
window.clearSelection = clearSelection;
