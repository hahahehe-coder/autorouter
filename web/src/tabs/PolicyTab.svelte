<script lang="ts">
  import type { ConfigSnapshot } from '../api';
  import { int as toInt, checked as getChecked } from '../lib/dom';

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
</div>

<style>
  .card-head-line { font-size: 15px; font-weight: 600; margin-bottom: 16px; }
  .policy-item { padding: 16px; border: 1px solid var(--line); border-radius: 8px; margin-bottom: 12px; background: var(--card); }
  .policy-item:last-child { margin-bottom: 0; }
  .switch input { width: 36px; height: 20px; }
  .switch input:disabled { opacity: 0.5; cursor: not-allowed; }
</style>