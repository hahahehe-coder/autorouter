<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { api, type ConfigSnapshot } from '../api';
  import { checked as getChecked } from '../lib/dom';

  export let snapshot: ConfigSnapshot;
  export let onChange: () => void = () => {};
  $: m = snapshot.ml ?? (snapshot.ml = { enabled: true, bundle_path: '', confidence_threshold: 0.5, confidence_fallback_idx: -1, warmup_on_load: true });

  // 实时 ML 状态(从 /health 拉,轮询;保存+热重载后会刷新)
  let mlStatus: any = null;
  let timer: any = null;
  async function refreshStatus() {
    try { const h = await api.health(); mlStatus = h.ml; } catch { /* 服务没起就忽略 */ }
  }
  onMount(() => { refreshStatus(); timer = setInterval(refreshStatus, 3000); });
  onDestroy(() => { if (timer) clearInterval(timer); });

  function setField(field: keyof typeof m, val: any) {
    (m as any)[field] = val;
    snapshot = snapshot; onChange();
  }
  // 取值 handler(as 类型断言只能放 script 里,Svelte 模板表达式不认 TS)
  const onEnabled = (e: Event) => setField('enabled', getChecked(e));
  const onBundlePath = (e: Event) => setField('bundle_path', (e.target as HTMLInputElement).value);
  const onThreshold = (e: Event) => setField('confidence_threshold', parseFloat((e.target as HTMLInputElement).value) || 0);
  const onFallbackIdx = (e: Event) => setField('confidence_fallback_idx', parseInt((e.target as HTMLInputElement).value));
  const STATUS_LABEL: Record<string, string> = {
    ready: '就绪', disabled: '已禁用', deps_missing: '依赖缺失', bundle_missing: 'bundle 缺失', runtime_error: '运行错误', unconfigured: '未配置',
  };
  const STATUS_COLOR: Record<string, string> = {
    ready: 'var(--success, #16a34a)', disabled: 'var(--ink-3)', deps_missing: 'var(--danger)', bundle_missing: 'var(--danger)', runtime_error: 'var(--danger)', unconfigured: 'var(--ink-3)',
  };
</script>

<div class="page-head">
  <h1>ML 路由</h1>
  <p>加载 OpenSquilla 预训练 bundle(LightGBM + BGE/ONNX,390 维特征)。依赖/bundle 缺失或推理出错时自动降级到启发式。</p>
</div>

<div class="card" style="margin-bottom: 16px;">
  <h2 class="card-head-line">运行状态</h2>
  {#if mlStatus}
    <div class="status-row">
      <span class="status-dot" style="background: {STATUS_COLOR[mlStatus.status] || 'var(--ink-3)'};"></span>
      <strong>{STATUS_LABEL[mlStatus.status] || mlStatus.status}</strong>
      {#if mlStatus.available}<span class="badge-ok">ML 生效中</span>{:else}<span class="badge-warn">回退启发式</span>{/if}
    </div>
    {#if mlStatus.reason}<p class="muted" style="margin: 6px 0 0; word-break: break-all;">原因:{mlStatus.reason}</p>{/if}
    <p class="muted" style="margin: 6px 0 0; word-break: break-all;">bundle:{mlStatus.bundle_path || '(未设)'}</p>
  {:else}
    <p class="muted">获取状态中…(服务未起时此项为空)</p>
  {/if}
</div>

<div class="card">
  <h2 class="card-head-line">配置</h2>

  <div class="policy-item">
    <div class="row-spread">
      <div>
        <strong>enabled</strong>
        <p class="muted" style="margin: 2px 0 0;">开启后优先用 ML 分类;关掉或加载失败则走启发式</p>
      </div>
      <label class="switch"><input type="checkbox" checked={m.enabled} on:change={onEnabled} /></label>
    </div>
  </div>

  <div class="field" style="margin-top: 12px;">
    <label class="field-label">bundle_path</label>
    <input class="mono" type="text" value={m.bundle_path} placeholder="OpenSquilla v4.2 bundle 目录(含 runtime_src/)"
      on:change={onBundlePath} />
  </div>

  <div class="field-row">
    <div class="field">
      <label class="field-label">confidence_threshold</label>
      <input type="number" step="0.05" min="0" max="1" value={m.confidence_threshold} on:change={onThreshold} />
      <p class="muted" style="font-size:12px;">ML max 概率低于此值 → 回退到 fallback 档</p>
    </div>
    <div class="field">
      <label class="field-label">confidence_fallback_idx</label>
      <input type="number" value={m.confidence_fallback_idx} on:change={onFallbackIdx} />
      <p class="muted" style="font-size:12px;">-1 = 最后一档(最强);否则字面 rule 索引</p>
    </div>
  </div>

  <p class="muted" style="margin-top: 12px; font-size: 12px;">
    启用 ML 需先在服务器装依赖:<code>uv sync --extra ml</code>(numpy/lightgbm/sklearn/joblib/onnxruntime/tokenizers,无 torch)。
    改完点底部"保存并热重载"即生效。
  </p>
</div>

<style>
  .card-head-line { font-size: 15px; font-weight: 600; margin-bottom: 16px; }
  .policy-item { padding: 16px; border: 1px solid var(--line); border-radius: 8px; background: var(--card); }
  .field-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px; }
  .status-row { display: flex; align-items: center; gap: 8px; }
  .status-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
  .badge-ok { font-size: 12px; padding: 2px 8px; border-radius: 10px; background: rgba(22,163,74,0.12); color: var(--success, #16a34a); }
  .badge-warn { font-size: 12px; padding: 2px 8px; border-radius: 10px; background: rgba(234,179,8,0.12); color: #ca8a04; }
  .switch input { width: 36px; height: 20px; }
</style>
