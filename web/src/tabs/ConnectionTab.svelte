<script lang="ts">
  import type { ConfigSnapshot } from '../api';
  import { val as v, int } from '../lib/dom';

  export let snapshot: ConfigSnapshot;
  export let onChange: () => void = () => {};

  $: c = snapshot.connection;

  function server(field: string, val: string) {
    (c.server as any)[field] = field === 'port' ? parseInt(val) || 3001 : val;
    snapshot = snapshot; onChange();
  }
  function upstream(field: string, val: string) {
    (c.upstream as any)[field] = val;
    snapshot = snapshot; onChange();
  }
</script>

<div class="page-head">
  <h1>连接</h1>
  <p>本服务监听 + 上游 LLM 服务。任何 OpenAI 兼容接口都支持(new-api / OpenRouter / 直连 Anthropic 等)。host=0.0.0.0 让上游经网关/公网IP回访到本服务;安全靠防火墙封 3001 外网入站。</p>
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
  <h2 class="card-head-line">上游</h2>
  <div class="field">
    <label class="field-label">base_url</label>
    <div class="field-help">回灌请求会发到这里,/api/models 也从这里拉。</div>
    <input class="mono" value={c.upstream.base_url} on:change={(e) => upstream('base_url', v(e))} />
  </div>
  <div class="field">
    <label class="field-label">api_key</label>
    <div class="field-help">Bearer token(用于 GET /v1/models)。留空仅影响"拉取模型",不影响回灌。密钥只存服务端,不出网。</div>
    <input class="mono" type="password" value={c.upstream.api_key} on:change={(e) => upstream('api_key', v(e))} />
  </div>
</div>

<style>
  .card-head-line { font-size: 15px; font-weight: 600; margin-bottom: 16px; }
</style>
