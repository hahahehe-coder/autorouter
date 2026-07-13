// API 类型 + 后端 client
// ConfigSnapshot 不再有 tiers,strategies.X 是 {kind, rule} 或 {kind, rules: []}

const BASE = '';

export interface RuleData {
  model: string;
  inference?: Record<string, any>;
}
export interface StrategyData {
  kind: 'static' | 'heuristic';
  rule?: RuleData;                 // static
  rules?: RuleData[];             // heuristic
}
export interface ConfigSnapshot {
  connection: any;
  policy: any;
  strategies: Record<string, StrategyData>;
  observability: any;
}

export interface RoutingPreview {
  strategy: string;
  rule_idx: number;
  rule_count: number;
  model: string;
  confidence: number;
  source: string;
  band: string;
  inference: Record<string, any>;
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
  preview:       (strategy: string, query: string) => json<RoutingPreview>(
    fetch(`${BASE}/api/route/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ strategy, query, messages: [{ role: 'user', content: query }] }),
    })),
};