-- shaderbase SQLite schema（DEV_PLAN §3.2）
-- 节点表：知识库的所有节点（Function/Struct/Uniform/Texture/CBuffer/Technique/SamplerState）

CREATE TABLE IF NOT EXISTS projects (
  name TEXT PRIMARY KEY,
  root_path TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS nodes (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  name TEXT,
  qualified_name TEXT,
  file_path TEXT NOT NULL,
  line INTEGER,
  start_col INTEGER,
  end_line INTEGER,
  end_col INTEGER,
  properties TEXT,          -- JSON
  conditional_signature TEXT,  -- 所在 #if 分支签名（PV 算）
  branch_family TEXT,        -- 分支家族键（PV 算）
  project TEXT NOT NULL,
  updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS edges (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,        -- CALLS/INCLUDES/HAS_MEMBER/DECLARES_UNIFORM/USES_UNIFORM/FLOWS_TO/IS_ENTRY_POINT/EXPOSES_TECHNIQUE/CONDITIONAL_ON
  source_file TEXT NOT NULL,
  source_line INTEGER,
  source_name TEXT,
  target_name TEXT,
  source_id INTEGER,         -- resolve 后填（0 = 未 resolve）
  target_id INTEGER,         -- resolve 后填（0 = 未 resolve）
  properties TEXT,           -- JSON（调用位置/resolve 结果/语义槽位等）
  conditional_signature TEXT,
  project TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS file_meta (
  file_path TEXT PRIMARY KEY,
  project TEXT NOT NULL,
  mtime INTEGER,
  size INTEGER,
  content_hash TEXT,
  node_count INTEGER,
  edge_count INTEGER,
  parsed_ok INTEGER DEFAULT 1,
  error_count INTEGER DEFAULT 0,
  last_indexed_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reverse_deps (
  source_file TEXT NOT NULL,
  dependent_file TEXT NOT NULL,
  dep_kind TEXT NOT NULL,
  project TEXT NOT NULL,
  PRIMARY KEY (source_file, dependent_file, dep_kind)
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_nodes_qname ON nodes(qualified_name);
CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
CREATE INDEX IF NOT EXISTS idx_nodes_project ON nodes(project);
CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_project ON edges(project);
