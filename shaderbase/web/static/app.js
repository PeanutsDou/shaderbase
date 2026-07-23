// shaderbase 知识图谱前端交互

const KIND_COLORS = {
  Function: '#4488ff',
  Struct: '#44ff88',
  Uniform: '#ffaa44',
  Texture: '#44ddff',
  SamplerState: '#ff6666',
  Technique: '#cc66ff',
  CBuffer: '#ffdd44',
};

const EDGE_STYLES = {
  CALLS: { 'line-color': '#4488ff', 'line-style': 'solid', width: 1.5 },
  INCLUDES: { 'line-color': '#606080', 'line-style': 'dashed', width: 1 },
  HAS_MEMBER: { 'line-color': '#44ff88', 'line-style': 'dotted', width: 1 },
  IS_ENTRY_POINT: { 'line-color': '#cc66ff', 'line-style': 'solid', width: 2.5 },
  EXPOSES_TECHNIQUE: { 'line-color': '#cc66ff', 'line-style': 'dashed', width: 1 },
};

let cy = null;
let activeKinds = new Set();

function initCytoscape() {
  cy = cytoscape({
    container: document.getElementById('cy'),
    elements: [],
    style: [
      {
        selector: 'node',
        style: {
          'background-color': ele => KIND_COLORS[ele.data('kind')] || '#888',
          'label': 'data(name)',
          'text-valign': 'bottom',
          'text-halign': 'center',
          'font-size': '11px',
          'color': '#e0e0e0',
          'width': 30,
          'height': 30,
          'border-width': ele => ele.data('cond') ? 3 : 0,
          'border-color': '#e94560',
        }
      },
      {
        selector: 'node:selected',
        style: {
          'border-width': 4,
          'border-color': '#ffdd44',
          'width': 40,
          'height': 40,
        }
      },
      {
        selector: 'edge',
        style: {
          'curve-style': 'bezier',
          'target-arrow-shape': 'triangle',
          'arrow-scale': 0.8,
          'font-size': '9px',
          'color': '#606080',
        }
      },
    ],
    layout: { name: 'cose', animate: true, padding: 30,
              nodeRepulsion: 8000, idealEdgeLength: 100 },
  });

  cy.on('tap', 'node', evt => {
    showDetail(evt.target.id());
    expandNeighbors(evt.target.id());
  });
}

async function api(path) {
  const res = await fetch(path);
  return res.json();
}

async function loadOverview() {
  const data = await api('/api/overview');
  let html = '';
  html += `<div class="stat-row"><span>文件数</span><span class="num">${data.file_count}</span></div>`;
  html += `<div class="stat-row"><span>节点总数</span><span class="num">${data.node_count}</span></div>`;
  html += `<div class="stat-row"><span>边总数</span><span class="num">${data.edge_count}</span></div>`;
  html += `<div class="stat-row"><span>条件分支节点</span><span class="num">${data.conditional_nodes}</span></div>`;
  html += `<div class="stat-row"><span>条件分支边</span><span class="num">${data.conditional_edges}</span></div>`;
  html += '<div style="margin-top:8px;font-size:12px;color:#a0a0c0;">节点类型:</div>';
  for (const [k, v] of Object.entries(data.nodes_by_kind)) {
    html += `<div class="stat-row"><span style="color:${KIND_COLORS[k]||'#888'}">● ${k}</span><span class="num">${v}</span></div>`;
  }
  document.getElementById('stats').innerHTML = html;

  // 类型过滤
  const filterDiv = document.getElementById('kind-filter');
  filterDiv.innerHTML = '';
  for (const kind of Object.keys(data.nodes_by_kind)) {
    const label = document.createElement('label');
    label.innerHTML = `<input type="checkbox" checked data-kind="${kind}"> <span style="color:${KIND_COLORS[kind]||'#888'}">${kind}</span>`;
    label.querySelector('input').addEventListener('change', applyKindFilter);
    filterDiv.appendChild(label);
  }
  activeKinds = new Set(Object.keys(data.nodes_by_kind));
}

function applyKindFilter() {
  activeKinds.clear();
  document.querySelectorAll('#kind-filter input').forEach(cb => {
    if (cb.checked) activeKinds.add(cb.dataset.kind);
  });
  cy.nodes().forEach(n => {
    if (activeKinds.has(n.data('kind'))) {
      n.style('display', 'element');
    } else {
      n.style('display', 'none');
    }
  });
}

async function doSearch() {
  const name = document.getElementById('search-box').value.trim();
  if (!name) return;
  const data = await api(`/api/search?name=${encodeURIComponent(name)}&limit=50`);
  if (data.nodes.length === 0) {
    setStatus(`没找到 "${name}"`);
    return;
  }
  resetGraph();
  addNodesToGraph(data.nodes, true);
  setStatus(`搜索 "${name}": ${data.count} 个节点`);
}

async function doTrace() {
  const name = document.getElementById('trace-box').value.trim();
  if (!name) return;
  const data = await api(`/api/subgraph?function=${encodeURIComponent(name)}&depth=3&limit=100`);
  if (data.nodes.length === 0) {
    setStatus(`没找到函数 "${name}"`);
    return;
  }
  resetGraph();
  // 加节点
  const nodeIds = new Set();
  data.nodes.forEach(n => {
    if (!cy.getElementById('n' + n.id).length) {
      cy.add({
        group: 'nodes',
        data: { id: 'n' + n.id, name: n.name, kind: n.kind,
                cond: !!n.conditional_signature,
                _data: n }
      });
    }
    nodeIds.add(n.name);
  });
  // 加边
  data.edges.forEach(e => {
    const eid = e.source + '->' + e.target + '_' + e.kind;
    if (cy.getElementById(eid).length) return;
    // 找 source/target 的 cy id
    const srcNode = cy.nodes().filter(n => n.data('name') === e.source);
    const tgtNode = cy.nodes().filter(n => n.data('name') === e.target);
    if (srcNode.length && tgtNode.length) {
      cy.add({
        group: 'edges',
        data: { id: eid, source: srcNode.id(), target: tgtNode.id(),
                kind: e.kind, cond: !!e.conditional_signature },
        ...EDGE_STYLES[e.kind]
      });
      cy.getElementById(eid).style(EDGE_STYLES[e.kind]);
    }
  });
  runLayout();
  setStatus(`调用链 "${name}": ${data.nodes.length} 节点, ${data.edges.length} 边${data.truncated ? ' (截断)' : ''}`);
}

function addNodesToGraph(nodes, doLayout) {
  nodes.forEach(n => {
    const id = 'n' + n.id;
    if (!cy.getElementById(id).length) {
      cy.add({
        group: 'nodes',
        data: { id, name: n.name, kind: n.kind,
                cond: !!n.conditional_signature,
                _data: n }
      });
    }
  });
  if (doLayout) runLayout();
}

async function expandNeighbors(nodeId) {
  const realId = nodeId.replace('n', '');
  const data = await api(`/api/neighbors/${realId}?limit=50`);
  let added = 0;
  // 加邻居节点
  data.nodes.forEach(n => {
    const id = 'n' + n.id;
    if (!cy.getElementById(id).length) {
      cy.add({
        group: 'nodes',
        data: { id, name: n.name, kind: n.kind,
                cond: !!n.conditional_signature, _data: n }
      });
      added++;
    }
  });
  // 加边
  data.edges.forEach(e => {
    const eid = e.source + '->' + e.target + '_' + e.kind + '_' + (e.line || '');
    if (cy.getElementById(eid).length) return;
    const srcNode = cy.nodes().filter(n => n.data('name') === e.source);
    const tgtNode = cy.nodes().filter(n => n.data('name') === e.target);
    if (srcNode.length && tgtNode.length) {
      cy.add({
        group: 'edges',
        data: { id: eid, source: srcNode.id(), target: tgtNode.id(),
                kind: e.kind, cond: !!e.conditional_signature },
      });
      cy.getElementById(eid).style(EDGE_STYLES[e.kind] || {});
    }
  });
  if (added > 0) {
    runLayout();
    setStatus(`展开 ${added} 个邻居`);
  }
}

async function showDetail(nodeId) {
  const realId = nodeId.replace('n', '');
  const node = await api(`/api/node/${realId}`);
  const conns = await api(`/api/node/${realId}/connections`);
  const panel = document.getElementById('detail-panel');
  const header = document.getElementById('detail-header');
  const body = document.getElementById('detail-body');

  const color = KIND_COLORS[node.kind] || '#888';
  let h = '';
  h += `<div class="kind-badge" style="background:${color}30;color:${color}">${node.kind}</div>`;
  h += `<h2>${node.name || '<anonymous>'}</h2>`;
  h += `<div class="file-line">${node.file_path}:${node.line}</div>`;
  if (node.conditional_signature) {
    h += `<div class="cond-sig">条件签名: ${node.conditional_signature}</div>`;
  }
  header.innerHTML = h;

  let b = '';
  // properties
  b += '<div class="section"><h4>属性</h4><div class="props">';
  b += JSON.stringify(node.properties, null, 2);
  b += '</div></div>';

  // connections
  b += '<div class="section"><h4>出边 (Outbound)</h4>';
  for (const [kind, items] of Object.entries(conns.outbound)) {
    b += `<div style="font-size:11px;color:${EDGE_STYLES[kind]?.['line-color']||'#888'};margin-top:4px;">${kind} (${items.length})</div>`;
    for (const item of items.slice(0, 15)) {
      const tgt = item.target || '?';
      const sig = item.conditional_signature ? ` <span class="sig">${item.conditional_signature}</span>` : '';
      b += `<div class="conn-item" onclick="searchByName('${tgt}')">${node.name} <span class="arrow">→</span> ${tgt}${sig}</div>`;
    }
  }
  b += '</div>';

  b += '<div class="section"><h4>入边 (Inbound)</h4>';
  for (const [kind, items] of Object.entries(conns.inbound)) {
    b += `<div style="font-size:11px;color:${EDGE_STYLES[kind]?.['line-color']||'#888'};margin-top:4px;">${kind} (${items.length})</div>`;
    for (const item of items.slice(0, 15)) {
      const src = item.source || '?';
      const sig = item.conditional_signature ? ` <span class="sig">${item.conditional_signature}</span>` : '';
      b += `<div class="conn-item" onclick="searchByName('${src}')">${src} <span class="arrow">→</span> ${node.name}${sig}</div>`;
    }
  }
  b += '</div>';

  // 源码按钮
  b += `<div class="section"><h4>源码</h4><button onclick="loadSource(${realId})">查看源码</button><div id="source-area"></div></div>`;

  body.innerHTML = b;
  panel.classList.add('active');
}

async function loadSource(nodeId) {
  const data = await api(`/api/source/${nodeId}?context=3`);
  const area = document.getElementById('source-area');
  if (data.error) {
    area.innerHTML = `<div style="color:#ff6666;font-size:12px;">${data.error}</div>`;
    return;
  }
  const lines = data.source.split('\n');
  let html = `<div style="font-size:11px;color:#808090;margin-bottom:4px;">${data.file_path} L${data.node_start}-${data.node_end}</div>`;
  html += '<div class="source">';
  lines.forEach((line, i) => {
    const lineNum = data.start_line + i;
    const isNode = lineNum >= data.node_start && lineNum <= data.node_end;
    const cls = isNode ? 'highlight' : '';
    html += `<div class="${cls}">${String(lineNum).padStart(4)}: ${line.replace(/</g,'&lt;')}</div>`;
  });
  html += '</div>';
  area.innerHTML = html;
}

function searchByName(name) {
  document.getElementById('search-box').value = name;
  doSearch();
}

function resetGraph() {
  cy.elements().remove();
  setStatus('');
}

function fitGraph() {
  cy.fit(undefined, 30);
}

function runLayout() {
  cy.layout({
    name: 'cose',
    animate: true,
    padding: 30,
    nodeRepulsion: 8000,
    idealEdgeLength: 80,
    nodeOverlap: 20,
    randomize: false,
    fit: false,
  }).run();
  applyKindFilter();
}

function setStatus(msg) {
  document.getElementById('status-bar').textContent = msg;
}

// 初始化
window.addEventListener('DOMContentLoaded', () => {
  initCytoscape();
  loadOverview();

  document.getElementById('search-box').addEventListener('keydown', e => {
    if (e.key === 'Enter') doSearch();
  });
  document.getElementById('trace-box').addEventListener('keydown', e => {
    if (e.key === 'Enter') doTrace();
  });
  document.getElementById('file-filter').addEventListener('input', e => {
    const pat = e.target.value.toLowerCase();
    cy.nodes().forEach(n => {
      const fp = n.data('_data')?.file_path || '';
      if (pat && !fp.toLowerCase().includes(pat)) {
        n.style('opacity', 0.15);
      } else {
        n.style('opacity', 1);
      }
    });
  });
});
