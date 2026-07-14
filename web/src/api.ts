// API 类型 + 后端 client
// 规则字段扁平化(model 跟其他字段一视同仁,后端按端点映射写 body)

const BASE = '';

export interface RuleData {
  model: string;
  max_tokens?: number;
  system?: string;
  thinking?: string;        // effort 字符串(各端点映射见 channel._apply_field)
}
export interface StrategyData {
  kind: 'static' | 'heuristic' | 'single' | 'rule' | 'classifier';
  rule?: RuleData;                 // single
  rules?: RuleData[];             // rule / classifier
}
export interface ModelEntry {
  supports_vision?: boolean | null;  // null=未知(capability_gate 不动)
  context_window?: number;
}
export interface MLCfg {
  enabled: boolean;
  bundle_path: string;
  confidence_threshold: number;
  confidence_fallback_idx: number;
  warmup_on_load?: boolean;
}
export interface ObservabilityCfg {
  log_dir: string;
}
export interface LogFile {
  name: string;
  size: number;
  mtime: number;
  is_today: boolean;
}
export interface LogContent {
  name: string;
  content: string;
  total_lines: number;
  size: number;
}
export interface ConfigSnapshot {
  connection: any;
  policy: any;
  strategies: Record<string, StrategyData>;
  observability: ObservabilityCfg;
  ml: MLCfg;
  models: Record<string, ModelEntry>;
}

export interface RoutingPreview {
  strategy: string;
  rule_idx: number;
  rule_count: number;
  model: string;
  confidence: number;
  source: string;
  band: string;
  fields: Record<string, any>;
  policies: Array<{ name: string; input_idx: number; output_idx: number; fired: boolean; info: string }>;
}

async function json<T>(req: Promise<Response>): Promise<T> {
  const r = await req;
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
}

export const api = {
  health:        () => json<any>(fetch(`${BASE}/health`)),
  getAll:        () => json<ConfigSnapshot>(fetch(`${BASE}/api/config`)),
  getSection:    (s: string) => json<any>(fetch(`${BASE}/api/config/${s}`)),
  putSection:    (s: string, data: any) => json<{ ok: boolean }>(
    fetch(`${BASE}/api/config/${s}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })),
  reload:        () => json<{ ok: boolean }>(fetch(`${BASE}/api/reload`, { method: 'POST' })),
  upstreamModels: () => json<string[]>(fetch(`${BASE}/api/models`)),
  listLogs:      () => json<{files: LogFile[]; log_dir: string}>(fetch(`${BASE}/api/logs`)),
  readLog:       (name: string) => json<LogContent>(fetch(`${BASE}/api/logs/${encodeURIComponent(name)}`)),
  preview:       (strategy: string, query: string) => json<RoutingPreview>(
    fetch(`${BASE}/api/route/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ strategy, query, messages: [{ role: 'user', content: query }] }),
    })),
};