<script lang="ts">
  import type { ConfigSnapshot } from '../api';
  import { int as toInt, num as toNum, checked as getChecked } from '../lib/dom';

  export let snapshot: ConfigSnapshot;
  export let onChange: () => void = () => {};
  $: p = snapshot.policy;

  function setAntiDowngrade(field: 'enabled' | 'window_seconds', val: any) {
    p.anti_downgrade ??= { enabled: true, window_seconds: 600 };
    (p.anti_downgrade as any)[field] = val;
    snapshot = snapshot; onChange();
  }
  function setComplaintUpgrade(field: 'enabled' | 'max_chars', val: any) {
    p.complaint_upgrade ??= { enabled: true, max_chars: 160 };
    (p.complaint_upgrade as any)[field] = val;
    snapshot = snapshot; onChange();
  }
  function setCapabilityGate(field: string, val: any) {
    p.capability_gate ??= { enabled: true };
    (p.capability_gate as any)[field] = val;
    snapshot = snapshot; onChange();
  }
  function setLargeContextFloor(field: string, val: any) {
    p.large_context_floor ??= { enabled: true, t3_floor_tokens: 100000, t2_floor_tokens: 50000, t3_context_ratio: 0.8, context_window: 128000 };
    (p.large_context_floor as any)[field] = val;
    snapshot = snapshot; onChange();
  }
</script>

<div class="page-head">
  <h1>后处理链</h1>
  <p>执行顺序在代码里写死。这里只管 开关 + 调参。</p>
</div>

<div class="card">
  <h2 class="card-head-line">3 个策略(顺序固定)</h2>

  <div class="policy-item">
    <div class="row-spread">
      <div>
        <strong>confidence_gate</strong>
        <p class="muted" style="margin: 2px 0 0;">classifier 输出 OOB → 退回最后一档</p>
      </div>
      <label class="switch"><input type="checkbox" disabled checked /></label>
    </div>
  </div>

  <div class="policy-item">
    <div class="row-spread">
      <div>
        <strong>complaint_upgrade</strong>
        <p class="muted" style="margin: 2px 0 0;">短消息中的抱怨词 → 升一档(往后走)</p>
      </div>
      <label class="switch"><input type="checkbox" checked={p.complaint_upgrade?.enabled ?? true}
        on:change={(e) => setComplaintUpgrade('enabled', getChecked(e))} /></label>
    </div>
    {#if p.complaint_upgrade?.enabled ?? true}
      <div class="field" style="margin-top: 10px;">
        <label class="field-label">max_chars</label>
        <input type="number" value={p.complaint_upgrade?.max_chars ?? 160}
          on:change={(e) => setComplaintUpgrade('max_chars', toInt(e) || 160)} />
      </div>
    {/if}
  </div>

  <div class="policy-item">
    <div class="row-spread">
      <div>
        <strong>anti_downgrade</strong>
        <p class="muted" style="margin: 2px 0 0;">会话窗口内禁降档,护住上游 KV cache</p>
      </div>
      <label class="switch"><input type="checkbox" checked={p.anti_downgrade?.enabled ?? true}
        on:change={(e) => setAntiDowngrade('enabled', getChecked(e))} /></label>
    </div>
    {#if p.anti_downgrade?.enabled ?? true}
      <div class="field" style="margin-top: 10px;">
        <label class="field-label">window_seconds</label>
        <input type="number" value={p.anti_downgrade?.window_seconds ?? 600}
          on:change={(e) => setAntiDowngrade('window_seconds', toInt(e) || 600)} />
      </div>
    {/if}
  </div>

  <div class="policy-item">
    <div class="row-spread">
      <div>
        <strong>capability_gate</strong>
        <p class="muted" style="margin: 2px 0 0;">含图片/上下文超窗 → 往上找能用的档。需在「策略」tab 给 rule 标 supports_vision/context_window,不标即 no-op(未知不动)</p>
      </div>
      <label class="switch"><input type="checkbox" checked={p.capability_gate?.enabled ?? true}
        on:change={(e) => setCapabilityGate('enabled', getChecked(e))} /></label>
    </div>
  </div>

  <div class="policy-item">
    <div class="row-spread">
      <div>
        <strong>large_context_floor</strong>
        <p class="muted" style="margin: 2px 0 0;">超大上下文强制高档(廉价模型装不下)。阈值是 token 口径</p>
      </div>
      <label class="switch"><input type="checkbox" checked={p.large_context_floor?.enabled ?? true}
        on:change={(e) => setLargeContextFloor('enabled', getChecked(e))} /></label>
    </div>
    {#if p.large_context_floor?.enabled ?? true}
      <div class="field-grid">
        <div class="field">
          <label class="field-label">t3_floor_tokens</label>
          <input type="number" value={p.large_context_floor?.t3_floor_tokens ?? 100000}
            on:change={(e) => setLargeContextFloor('t3_floor_tokens', toInt(e) || 100000)} />
        </div>
        <div class="field">
          <label class="field-label">t2_floor_tokens</label>
          <input type="number" value={p.large_context_floor?.t2_floor_tokens ?? 50000}
            on:change={(e) => setLargeContextFloor('t2_floor_tokens', toInt(e) || 50000)} />
        </div>
        <div class="field">
          <label class="field-label">t3_context_ratio</label>
          <input type="number" step="0.05" value={p.large_context_floor?.t3_context_ratio ?? 0.8}
            on:change={(e) => setLargeContextFloor('t3_context_ratio', toNum(e) || 0.8)} />
        </div>
        <div class="field">
          <label class="field-label">context_window</label>
          <input type="number" value={p.large_context_floor?.context_window ?? 128000}
            on:change={(e) => setLargeContextFloor('context_window', toInt(e) || 128000)} />
        </div>
      </div>
    {/if}
  </div>
</div>

<style>
  .card-head-line { font-size: 15px; font-weight: 600; margin-bottom: 16px; }
  .policy-item { padding: 16px; border: 1px solid var(--line); border-radius: 8px; margin-bottom: 12px; background: var(--card); }
  .policy-item:last-child { margin-bottom: 0; }
  .switch input { width: 36px; height: 20px; }
  .switch input:disabled { opacity: 0.5; cursor: not-allowed; }
  .field-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 10px; }
</style>