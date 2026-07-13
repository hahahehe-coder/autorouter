<script lang="ts">
  import type { ConfigSnapshot } from '../api';
  import { val as v, int, selVal } from '../lib/dom';
  export let snapshot: ConfigSnapshot;
  export let onChange: () => void = () => {};
  $: o = snapshot.observability;
</script>

<div class="page-head">
  <h1>观测</h1>
  <p>路由决策的结构化日志 + 可选 JSONL(给离线训练用)。</p>
</div>

<div class="card">
  <div class="field">
    <label class="field-label">decision_log</label>
    <div class="field-help">每次路由打一条 INFO 级别的结构化日志</div>
    <select value={o.decision_log ? 'true' : 'false'} on:change={(e) => { o.decision_log = selVal(e) === 'true'; snapshot = snapshot; onChange(); }}>
      <option value="true">true</option>
      <option value="false">false</option>
    </select>
  </div>

  <div class="field">
    <label class="field-label">jsonl_path</label>
    <div class="field-help">留空 = 不落盘。设路径则每个决策额外追写一行 JSONL(给后续训练 LightGBM 用)。绝不存原文。</div>
    <input class="mono" value={o.jsonl_path ?? ''} placeholder="如 ./data/routing.jsonl" on:change={(e) => { o.jsonl_path = v(e) || null; snapshot = snapshot; onChange(); }} />
  </div>

  <div class="field">
    <label class="field-label">log_preview_chars</label>
    <div class="field-help">日志里 query 预览的字符数(只看前 N 字,完整 prompt 不出日志)</div>
    <input type="number" value={o.log_preview_chars} on:change={(e) => { o.log_preview_chars = int(e) || 80; snapshot = snapshot; onChange(); }} />
  </div>
</div>
