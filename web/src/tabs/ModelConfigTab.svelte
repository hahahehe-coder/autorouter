<script lang="ts">
  import { api, type ConfigSnapshot } from '../api';
  import { selVal, val as inputVal } from '../lib/dom';

  export let snapshot: ConfigSnapshot;
  export let onChange: () => void = () => {};
  $: m = snapshot.models ?? (snapshot.models = {});

  let pulling = false;
  let pullError = '';

  async function pullUpstream() {
    const providers = Object.entries(snapshot?.connection?.providers ?? {})
      .filter(([k, v]) => k !== 'default' && (v as any)?.api_key);
    if (providers.length === 0) {
      pullError = '请先在「连接」至少给一个供应商配置 api_key';
      return;
    }
    pulling = true; pullError = '';
    try {
      const result = await api.upstreamModels();
      // 客户端 merge:新增模型补进注册表(保留已有标注 + upstream tag),已删的保留。
      let added = 0, retagged = 0;
      for (const { id, upstream } of result.models) {
        if (!m[id]) {
          m[id] = { supports_vision: null, context_window: null, upstream };
          added++;
        } else if (!m[id].upstream && upstream) {
          m[id].upstream = upstream; retagged++;
        }
      }
      snapshot = snapshot; onChange();
      const parts = [`新增 ${added}`];
      if (retagged) parts.push(`补 tag ${retagged}`);
      if (result.errors.length) parts.push(`失败 ${result.errors.length} 个供应商`);
      pullError = added || retagged ? parts.join(',') : (result.errors.length ? result.errors.join('; ') : '已是最新');
    } catch (e: any) {
      pullError = e.message;
    } finally {
      pulling = false;
    }
  }

  function setVision(name: string, e: Event) {
    const v = selVal(e);
    m[name].supports_vision = v === 'true' ? true : v === 'false' ? false : null;
    snapshot = snapshot; onChange();
  }
  function setContextWindow(name: string, e: Event) {
    const v = inputVal(e);
    m[name].context_window = v === '' ? null : parseInt(v);
    snapshot = snapshot; onChange();
  }
  function removeModel(name: string) {
    if (!confirm(`从注册表删除 "${name}"?(引用它的策略 rule 会变成无效模型)`)) return;
    delete m[name];
    snapshot = snapshot; onChange();
  }
  let newName = '';
  function addModel() {
    const n = newName.trim();
    if (!n || m[n]) { if (m[n]) alert('已存在'); return; }
    m[n] = { supports_vision: null, context_window: null, upstream: '' };
    newName = '';
    snapshot = snapshot; onChange();
  }

  $: modelNames = Object.keys(m).sort();
  function visionStr(v: boolean | null | undefined): string {
    return v === true ? 'true' : v === false ? 'false' : '';
  }
</script>

<div class="page-head">
  <h1>模型注册表</h1>
  <p>拉上游后在此标注每个模型的能力(capability_gate 按模型名查这里判断升档)。策略 rule 只填模型名,能力集中在这里管理。</p>
</div>

<div class="card" style="margin-bottom: 16px;">
  <h2 class="card-head-line">拉取上游模型</h2>
  <p class="muted" style="margin: 0 0 10px;">从上游拉模型列表,新增的会补进注册表(已有标注保留);删除上游不会自动从注册表移除。</p>
  <button class="btn btn-secondary" on:click={pullUpstream} disabled={pulling}>
    {pulling ? '拉取中…' : '拉取上游'}
  </button>
  {#if pullError}<span class="muted" style="margin-left: 12px;">{pullError}</span>{/if}
</div>

<div class="card" style="margin-bottom: 16px;">
  <h2 class="card-head-line">已注册模型 <span class="meta">{modelNames.length} 个</span></h2>
  {#if modelNames.length === 0}
    <p class="muted">还没有模型 —— 点上方"拉取上游"或下方手动添加。</p>
  {:else}
    <table class="model-table">
      <thead>
        <tr><th>模型名</th><th>供应商</th><th>supports_vision</th><th>context_window</th><th></th></tr>
      </thead>
      <tbody>
        {#each modelNames as name}
          <tr>
            <td><code class="mono">{name}</code></td>
            <td>
              {#if m[name].upstream}
                <span class="badge">{m[name].upstream}</span>
              {:else}
                <span class="muted">默认</span>
              {/if}
            </td>
            <td>
              <select class="mono" value={visionStr(m[name].supports_vision)} on:change={(e) => setVision(name, e)}>
                <option value="">未知(不动)</option>
                <option value="false">否</option>
                <option value="true">是</option>
              </select>
            </td>
            <td>
              <input class="mono" type="number" value={m[name].context_window ?? ''} placeholder="留空=未知"
                on:change={(e) => setContextWindow(name, e)} style="width: 110px;" />
            </td>
            <td><button class="btn-ghost btn-sm" on:click={() => removeModel(name)}>删除</button></td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>

<div class="card">
  <h2 class="card-head-line">手动添加模型</h2>
  <div style="display: flex; gap: 8px; align-items: center;">
    <input class="mono" type="text" placeholder="模型名(精确匹配上游)" bind:value={newName}
      on:keydown={(e) => { if (e.key === 'Enter') addModel(); }} style="flex: 1;" />
    <button class="btn btn-primary" on:click={addModel} disabled={!newName.trim()}>添加</button>
  </div>
</div>

<style>
  .card-head-line { font-size: 15px; font-weight: 600; margin-bottom: 16px; }
  .meta { color: var(--ink-3); font-weight: normal; font-size: 13px; }
  .model-table { width: 100%; border-collapse: collapse; }
  .model-table th, .model-table td { padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--line); }
  .model-table th { color: var(--ink-3); font-weight: 500; font-size: 13px; }
  .model-table code { background: var(--bg); padding: 2px 6px; border-radius: 4px; }
  .badge { background: var(--bg); color: var(--ink-2); padding: 2px 8px; border-radius: 4px; font-size: 12px; font-family: var(--mono); }
  .muted { color: var(--ink-3); font-size: 13px; }
</style>
