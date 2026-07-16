// API 类型 + 后端 client
// 规则字段扁平化(model 跟其他字段一视同仁,后端按端点映射写 body)

const BASE = '';
const AUTH_KEY = 'autorouter-basic-auth';

// 内存 + localStorage 保存 Basic Auth 凭证(base64("user:pass"))
function loadAuth(): string | null {
  try { return localStorage.getItem(AUTH_KEY); } catch { return null; }
}
function saveAuth(b64: string | null) {
  try {
    if (b64) localStorage.setItem(AUTH_KEY, b64);
    else localStorage.removeItem(AUTH_KEY);
  } catch { /* localStorage may be disabled */ }
}
export function setAuth(user: string, pass: string) {
  // btoa 在非 ASCII user/password 上会失败 — 用 encodeURIComponent 保险一下
  const b64 = btoa(unescape(encodeURIComponent(`${user}:${pass}`)));
  saveAuth(b64);
  notifyAuthChange();
}
export function clearAuth() {
  saveAuth(null);
  notifyAuthChange();
}
export function getAuthB64(): string | null {
  return loadAuth();
}
// 监听凭证变更(SPA 其它组件若要刷新可以监听)
type AuthListener = () => void;
const _authListeners: Set<AuthListener> = new Set();
export function onAuthChange(fn: AuthListener): () => void {
  _authListeners.add(fn);
  return () => _authListeners.delete(fn);
}
function notifyAuthChange() {
  _authListeners.forEach((fn) => fn());
}

// 401 时触发(让 App.svelte 弹 LoginScreen)
type Auth401Listener = () => void;
const _auth401Listeners: Set<Auth401Listener> = new Set();
export function onAuth401(fn: Auth401Listener): () => void {
  _auth401Listeners.add(fn);
  return () => _auth401Listeners.delete(fn);
}
function notifyAuth401() {
  _auth401Listeners.forEach((fn) => fn());
}

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
  context_window?: number | null;
  upstream?: string;             // 拉取时自动填的供应商名(UI 不编辑)
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
export interface AdminCfg {
  user: string;
  enabled: boolean;            // password 是否配了(纯由后端判断)
  password?: string;           // 仅提交新密码时出现;后端不会回显
}
export interface ProviderEntry {
  base_url: string;
  api_key: string;
}
export interface ConnectionCfg {
  providers: { default: string } & Record<string, ProviderEntry>;
  admin: AdminCfg;
}
export interface PullResult {
  models: Array<{ id: string; upstream: string }>;
  errors: string[];
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
  connection: ConnectionCfg;
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
  if (r.status === 401) {
    clearAuth();                   // bad/expired creds → 清掉,触发 LoginScreen
    notifyAuth401();
    throw new Error('auth required');
  }
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
}

// 给 fetch 包装一层,自动附带 Authorization 头(只对 /api/*,/v1/* 不需要)
function authedFetch(path: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers || {});
  if (path.startsWith('/api/') && path !== '/api/health') {
    const b64 = getAuthB64();
    if (b64) headers.set('Authorization', `Basic ${b64}`);
  }
  return fetch(`${BASE}${path}`, { ...init, headers });
}

export const api = {
  health:        () => json<any>(fetch(`${BASE}/health`)),
  getAll:        () => json<ConfigSnapshot>(authedFetch('/api/config')),
  putAll:        (data: ConfigSnapshot) => json<{ ok: boolean }>(
    authedFetch('/api/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })),
  getSection:    (s: string) => json<any>(authedFetch(`/api/config/${s}`)),
  putSection:    (s: string, data: any) => json<{ ok: boolean }>(
    authedFetch(`/api/config/${s}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })),
  reload:        () => json<{ ok: boolean }>(authedFetch('/api/reload', { method: 'POST' })),
  upstreamModels: () => json<PullResult>(authedFetch('/api/models')),
  listLogs:      () => json<{files: LogFile[]; log_dir: string}>(authedFetch('/api/logs')),
  readLog:       (name: string) => json<LogContent>(authedFetch(`/api/logs/${encodeURIComponent(name)}`)),
  preview:       (strategy: string, query: string) => json<RoutingPreview>(
    authedFetch('/api/route/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ strategy, query, messages: [{ role: 'user', content: query }] }),
    })),
};
