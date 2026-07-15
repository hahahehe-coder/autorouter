<script lang="ts">
  import type { ConfigSnapshot } from '../api';
  import { val as v, int } from '../lib/dom';

  export let snapshot: ConfigSnapshot;
  export let onChange: () => void = () => {};

  $: c = snapshot.connection;
  // providers 顶层有 `default` 特殊字段 + 其他 name → ProviderEntry。
  // 把它们拆开:providerNames 是真实供应商列表,default 是回退名。
  $: providerEntries = Object.entries(c.providers ?? {})
      .filter(([k]) => k !== 'default') as [string, { base_url: string; api_key: string }][];
  $: defaultName = c.providers?.default ?? '';
  // 默认名指向真实存在的供应商;否则取第一个
  $: effectiveDefault = providerEntries.find(([n]) => n === defaultName)
      ? defaultName
      : (providerEntries[0]?.[0] ?? '');

  function server(field: string, val: string) {
    (c.server as any)[field] = field === 'port' ? parseInt(val) || 3001 : val;
    snapshot = snapshot; onChange();
  }
  function setProvider(oldName: string, newName: string, base: string, key: string) {
    const providers = c.providers;
    const entry = (providers as any)[oldName];
    delete providers[oldName];
    (providers as any)[newName] = { base_url: base, api_key: key };
    if (providers.default === oldName) providers.default = newName || effectiveDefault;
    snapshot = snapshot; onChange();
  }
  function setDefault(name: string) {
    c.providers.default = name;
    snapshot = snapshot; onChange();
  }
  function addProvider() {
    const base = 'new_provider_' + Math.random().toString(36).slice(2, 7);
    (c.providers as any)[base] = { base_url: '', api_key: '' };
    snapshot = snapshot; onChange();
  }
  function removeProvider(name: string) {
    if (!confirm(`删除供应商 "${name}"?引用它的模型将回退到默认供应商。`)) return;
    delete (c.providers as any)[name];
    if (c.providers.default === name) {
      c.providers.default = Object.keys(c.providers).find((k) => k !== 'default') ?? '';
    }
    snapshot = snapshot; onChange();
  }
  // 给每行生成一个稳定 id,key 改时不重渲染
  function rowId(_name: string, _i: number) { return _i; }
</script>

<div class="page-head">
  <h1>连接</h1>
  <p>本服务监听 + 多个上游 LLM 供应商。每个模型从哪个供应商拉来就自动走哪个供应商转发(没有 tag 的走默认)。</p>
  <p class="muted">支持 OpenAI 兼容接口(new-api / OpenRouter / 直连 Anthropic 等);同一供应商下所有模型共享一个 key。</p>
</div>

<div class="card">
  <h2 class="card-head-line">本服务</h2>
  <div class="field">
    <label class="field-label">host</label>
    <input class="mono" value={c.server.host} on:change={(e) => server('host', v(e))} />
  </div>
  <div class="field">
    <label class="field-label">port</label>
    <input class="mono" type="number" value={c.server.port} on:change={(e) => server('port', v(e))} />
  </div>
</div>

<div class="card">
  <h2 class="card-head-line">上游供应商</h2>
  {#if providerEntries.length === 0}
    <p class="muted">还没有配置供应商,点下面按钮加一个。</p>
  {/if}
  {#each providerEntries as [name, p], i (rowId(name, i))}
    <div class="prov-row">
      <div class="prov-default">
        <input type="radio" name="default-provider" checked={name === effectiveDefault}
               on:change={() => setDefault(name)} title="默认供应商(模型没 tag 时回退)" />
      </div>
      <div class="prov-fields">
        <div class="field">
          <label class="field-label">name</label>
          <input class="mono" value={name}
                 on:change={(e) => setProvider(name, v(e), p.base_url, p.api_key)} />
        </div>
        <div class="field">
          <label class="field-label">base_url</label>
          <input class="mono" value={p.base_url}
                 on:change={(e) => setProvider(name, name, v(e), p.api_key)} />
        </div>
        <div class="field">
          <label class="field-label">api_key</label>
          <input class="mono" type="password" value={p.api_key}
                 on:change={(e) => setProvider(name, name, p.base_url, v(e))} />
        </div>
      </div>
      <button class="btn btn-danger" on:click={() => removeProvider(name)}>删</button>
    </div>
  {/each}
  <button class="btn btn-secondary" on:click={addProvider}>+ 加供应商</button>
</div>

<style>
  .card-head-line { font-size: 15px; font-weight: 600; margin-bottom: 16px; }
  .prov-row {
    display: grid;
    grid-template-columns: 24px 1fr auto;
    gap: 12px;
    align-items: start;
    padding: 12px 0;
    border-bottom: 1px solid #eee;
  }
  .prov-row:last-of-type { border-bottom: none; }
  .prov-default { padding-top: 28px; }
  .prov-fields { display: grid; gap: 10px; }
  .btn-danger {
    background: #fff; color: #c33; border: 1px solid #c33;
    padding: 4px 10px; border-radius: 4px; cursor: pointer;
    margin-top: 24px;
  }
  .btn-danger:hover { background: #fee; }
</style>
