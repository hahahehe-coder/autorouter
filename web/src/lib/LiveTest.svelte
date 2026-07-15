<script lang="ts">
  import { api, type RoutingPreview } from '../api';

  let strategy = '';
  let query = '';
  let preview: RoutingPreview | null = null;
  let error = '';

  async function run() {
    if (!strategy || !query) { preview = null; return; }
    try {
      preview = await api.preview(strategy, query);
      error = '';
    } catch (e: any) {
      error = e.message;
      preview = null;
    }
  }

  export let availableStrategies: string[] = [];
</script>

<div class="card">
  <h3 class="card-head-sub">实时路由测试</h3>
  <p class="muted" style="margin-bottom: 12px;">
    选策略、输入 query,点击"测试"看路由决策(不调上游,只跑路由管道)。
  </p>

  <div style="display: grid; grid-template-columns: 200px 1fr auto; gap: 12px; align-items: end; margin-bottom: 12px;">
    <div class="field" style="margin: 0;">
      <label class="field-label">策略</label>
      <select bind:value={strategy}>
        <option value="">— 选一个 —</option>
        {#each availableStrategies as s}
          <option value={s}>{s}</option>
        {/each}
      </select>
    </div>
    <div class="field" style="margin: 0;">
      <label class="field-label">Query</label>
      <input class="mono" bind:value={query} placeholder="例如:写个 python 快排 / why is the sky blue" />
    </div>
    <button class="btn btn-primary" on:click={run}>测试</button>
  </div>

  {#if error}
    <div class="test-out" style="border-color: var(--danger); background: var(--danger-soft); color: var(--danger);">
      {error}
    </div>
  {:else if preview}
    <div class="test-out has-result">
策略     <code>{preview.strategy}</code>
路由源   <code style="color: {preview.source === 'ml' ? 'var(--accent)' : 'inherit'};">{preview.source}</code>{#if preview.band} ({preview.band}){/if}
档位     <code>{preview.rule_idx}</code>
模型     <code>{preview.model}</code>
置信度   <code>{(preview.confidence ?? 0).toFixed(2)}</code>

策略链:
{#each preview.policies as p}
  · <strong>{p.name}</strong>{#if p.fired} 🔥({p.input_idx}→{p.output_idx} · {p.info}){:else} ({p.input_idx}){/if}
{/each}
    </div>
  {:else}
    <div class="test-out muted">
      还没跑测试。选择策略 + 输入 query。
    </div>
  {/if}
</div>

<style>
  .card-head-sub { font-size: 15px; font-weight: 600; }
</style>
