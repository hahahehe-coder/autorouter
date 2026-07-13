<script lang="ts">
  import LiveTest from '../lib/LiveTest.svelte';
  import ModelSelect from '../lib/ModelSelect.svelte';
  import type { ConfigSnapshot, StrategyData } from '../api';
  import { selVal } from '../lib/dom';

  export let snapshot: ConfigSnapshot;
  export let dirty = false;
  export let upstreamModels: string[] = [];

  export let onChange: () => void = () => {};

  let showAddStrategy = false;
  let newStrategyName = '';

  // classifier 输出下标 → 人类标签(给 UI 显示用)
  const CLASSIFIER_LABELS = ['0 trivial', '1 medium', '2 code', '3 heavy'];

  function commitNewStrategy() {
    const name = newStrategyName.trim();
    if (!name || snapshot.strategies[name]) {
      if (snapshot.strategies[name]) alert('策略名已存在');
      return;
    }
    snapshot.strategies[name] = {
      kind: 'heuristic',
      rules: [{ model: '' }, { model: '' }, { model: '' }, { model: '' }],
    };
    newStrategyName = '';
    showAddStrategy = false;
    snapshot = snapshot; onChange();
  }
  function rename(oldName: string, ev: Event) {
    const nu = (ev.target as HTMLInputElement).value.trim();
    if (!nu || nu === oldName) { snapshot = snapshot; return; }
    if (snapshot.strategies[nu]) { alert('策略名已存在'); snapshot = snapshot; return; }
    const ordered: Record<string, StrategyData> = {};
    for (const k of Object.keys(snapshot.strategies)) {
      ordered[k === oldName ? nu : k] = snapshot.strategies[k];
    }
    snapshot.strategies = ordered;
    snapshot = snapshot; onChange();
  }
  function remove(name: string) {
    if (!confirm(`删除策略 "${name}"?`)) return;
    delete snapshot.strategies[name];
    snapshot = snapshot; onChange();
  }
  function changeKind(name: string, kind: 'static' | 'heuristic') {
    const s = snapshot.strategies[name];
    s.kind = kind;                                   // 必须写,模板靠它切分支
    if (kind === 'static') {
      s.rule = { model: '' };
      delete s.rules;
    } else {
      s.rules = (s.rules && s.rules.length >= 2) ? s.rules : [{ model: '' }, { model: '' }];
      delete s.rule;
    }
    snapshot = snapshot; onChange();
  }
  function onKindSelect(name: string) {
    return (e: Event) => changeKind(name, selVal(e) as 'static' | 'heuristic');
  }

  // --- static rule ---
  function setStaticModel(name: string, e: Event) {
    if (!snapshot.strategies[name].rule) snapshot.strategies[name].rule = { model: '' };
    snapshot.strategies[name].rule!.model = (e.target as HTMLInputElement).value;
    snapshot = snapshot; onChange();
  }
  function addStaticInference(name: string) {
    if (!snapshot.strategies[name].rule) snapshot.strategies[name].rule = { model: '' };
    snapshot.strategies[name].rule!.inference ??= {};
    const key = prompt('字段名(例如 max_tokens / reasoning_effort):');
    if (key) {
      snapshot.strategies[name].rule!.inference![key] = '';
      snapshot = snapshot; onChange();
    }
  }
  function removeStaticInference(name: string, key: string) {
    delete snapshot.strategies[name].rule!.inference![key];
    snapshot = snapshot; onChange();
  }
  function onStaticInfValInput(name: string, key: string, e: Event) {
    snapshot.strategies[name].rule!.inference![key] = (e.target as HTMLInputElement).value;
    snapshot = snapshot;
  }

  // --- heuristic rules ---
  function onRuleModelInput(name: string, idx: number, e: Event) {
    snapshot.strategies[name].rules![idx].model = (e.target as HTMLInputElement).value;
    snapshot = snapshot; onChange();
  }
  function addRuleInference(name: string, idx: number) {
    snapshot.strategies[name].rules![idx].inference ??= {};
    const key = prompt('字段名:');
    if (key) {
      snapshot.strategies[name].rules![idx].inference![key] = '';
      snapshot = snapshot; onChange();
    }
  }
  function removeRuleInference(name: string, idx: number, key: string) {
    delete snapshot.strategies[name].rules![idx].inference![key];
    snapshot = snapshot; onChange();
  }
  function onRuleInfValInput(name: string, idx: number, key: string, e: Event) {
    snapshot.strategies[name].rules![idx].inference![key] = (e.target as HTMLInputElement).value;
    snapshot = snapshot;
  }
  function addRule(name: string) {
    snapshot.strategies[name].rules!.push({ model: '' });
    snapshot = snapshot; onChange();
  }
  function removeRule(name: string, idx: number) {
    snapshot.strategies[name].rules!.splice(idx, 1);
    snapshot = snapshot; onChange();
  }
  function moveRule(name: string, idx: number, dir: -1 | 1) {
    const arr = snapshot.strategies[name].rules!;
    const j = idx + dir;
    if (j < 0 || j >= arr.length) return;
    [arr[idx], arr[j]] = [arr[j], arr[idx]];
    snapshot = snapshot; onChange();
  }
  function lastRuleIdx(name: string): number {
    return (snapshot.strategies[name].rules?.length ?? 1) - 1;
  }

  function blurOnEnter(e: KeyboardEvent) {
    if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
  }

  $: names = Object.keys(snapshot.strategies ?? {});
</script>

<div class="page-head">
  <h1>策略</h1>
  <p>每个策略对应 new-api 后台 channel "模型"字段里的一个名字。用户请求 model=&lt;名&gt; 时走对应规则。</p>
</div>

<LiveTest availableStrategies={names} />

<div class="card" style="margin-top: 16px;">
  <div class="card-head">
    <h2>策略列表 <span class="meta">{names.length} 个</span></h2>
    {#if !showAddStrategy}
      <button class="btn btn-secondary" on:click={() => showAddStrategy = true}>+ 新增策略</button>
    {:else}
      <span style="display: flex; gap: 6px; align-items: center;">
        <input class="mono" type="text" placeholder="策略名(英文)" bind:value={newStrategyName}
          on:keydown={(e) => { if (e.key === 'Enter') commitNewStrategy(); if (e.key === 'Escape') { showAddStrategy = false; newStrategyName = ''; } }} />
        <button class="btn btn-primary" style="height: 32px; padding: 0 12px;" on:click={commitNewStrategy}>添加</button>
        <button class="btn-ghost btn-sm" on:click={() => { showAddStrategy = false; newStrategyName = ''; }}>取消</button>
      </span>
    {/if}
  </div>

  {#if names.length === 0}
    <p class="muted">还没有策略,点右上角"新增策略"开始。</p>
  {:else}
    {#each names as n (n)}
      <div class="strategy-item" data-strat={n}>
        <div class="strategy-head">
          <div class="field">
            <label class="field-label">策略名</label>
            <input class="mono" type="text" value={n} on:change={(e) => rename(n, e)} on:keydown={blurOnEnter} />
          </div>
          <div class="field">
            <label class="field-label">路由模式</label>
            <select on:change={onKindSelect(n)}>
              <option value="static" selected={snapshot.strategies[n].kind === 'static'}>static — 单一模型</option>
              <option value="heuristic" selected={snapshot.strategies[n].kind === 'heuristic'}>heuristic — 按难度分档</option>
            </select>
          </div>
          <div></div>
          <div>
            <button class="btn btn-danger" on:click={() => remove(n)}>删除</button>
          </div>
        </div>

        {#if snapshot.strategies[n].kind === 'static'}
          <div class="field">
            <label class="field-label">模型</label>
            <ModelSelect value={snapshot.strategies[n].rule?.model ?? ''} {upstreamModels}
              on:change={(e) => setStaticModel(n, e)} />
          </div>
          <div class="field">
            <label class="field-label">inference(注入 body,覆盖用户同名字段)</label>
            <div style="display: flex; flex-wrap: wrap; gap: 6px; align-items: center;">
              {#each Object.entries(snapshot.strategies[n].rule?.inference ?? {}) as [k, v] (k)}
                <span class="inference-row">
                  <code>{k}</code>
                  <span class="equals">=</span>
                  <input class="mono inference-val" type="text" value={String(v)}
                    on:change={(e) => onStaticInfValInput(n, k, e)} />
                  <button class="btn-danger btn-sm" on:click={() => removeStaticInference(n, k)}>删除</button>
                </span>
              {/each}
              <button class="btn-ghost" style="font-size: 12px; height: 28px;" on:click={() => addStaticInference(n)}>+ 字段</button>
            </div>
          </div>
        {:else}
          <p class="muted" style="margin: 0 0 8px;">
            数组下标 = classifier 输出(0 trivial / 1 medium / 2 code / 3 heavy)。越往后能力越强。
          </p>
          {#each snapshot.strategies[n].rules ?? [] as r, i (i)}
            <div class="rule-card">
              <div class="rule-card-head">
                <span class="rule-num">{CLASSIFIER_LABELS[i] ?? `rule ${i}`}</span>
                <div class="rule-card-actions">
                  {#if i > 0}<button class="btn-ghost btn-sm" on:click={() => moveRule(n, i, -1)}>上移</button>{/if}
                  {#if i < lastRuleIdx(n)}<button class="btn-ghost btn-sm" on:click={() => moveRule(n, i, 1)}>下移</button>{/if}
                  <button class="btn-danger btn-sm" on:click={() => removeRule(n, i)}>删除</button>
                </div>
              </div>

              <div class="field">
                <label class="field-label">模型</label>
                <ModelSelect value={r.model ?? ''} {upstreamModels} on:change={(e) => onRuleModelInput(n, i, e)} />
              </div>

              <div class="field">
                <label class="field-label">inference</label>
                <div style="display: flex; flex-wrap: wrap; gap: 6px; align-items: center;">
                  {#each Object.entries(r.inference ?? {}) as [k, v] (k)}
                    <span class="inference-row">
                      <code>{k}</code>
                      <span class="equals">=</span>
                      <input class="mono inference-val" type="text" value={String(v)}
                        on:change={(e) => onRuleInfValInput(n, i, k, e)} />
                      <button class="btn-danger btn-sm" on:click={() => removeRuleInference(n, i, k)}>删除</button>
                    </span>
                  {/each}
                  <button class="btn-ghost" style="font-size: 12px; height: 28px;" on:click={() => addRuleInference(n, i)}>+ 字段</button>
                </div>
              </div>
            </div>
          {/each}
          <button class="btn-ghost" on:click={() => addRule(n)} style="font-size: 13px; margin-top: 8px;">+ 添加 rule</button>
        {/if}
      </div>
    {/each}
  {/if}
</div>

<style>
  .btn-icon { background: transparent; border: none; color: var(--ink-3); cursor: pointer; padding: 4px 8px; font-size: 16px; }
  .btn-icon:hover { color: var(--danger); }

  .rule-card {
    background: var(--bg);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 14px 16px;
    margin-bottom: 10px;
  }
  .rule-card-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
  }
  .rule-num {
    font-family: var(--font-mono);
    font-size: 13px;
    font-weight: 600;
    color: var(--accent);
  }
  .rule-card-actions { display: flex; gap: 4px; align-items: center; }

  .inference-row {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 4px 8px;
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 6px;
    font-family: var(--font-mono);
    font-size: 12.5px;
  }
  .inference-row .equals { color: var(--ink-3); }
  .inference-row code { color: var(--accent); font-weight: 500; }
  .inference-row .inference-val { width: 100px; height: 22px; padding: 0 6px; font-size: 12px; }
  .chip-x { background: transparent; border: none; color: var(--ink-3); cursor: pointer; font-size: 14px; padding: 0 4px; line-height: 1; }
  .chip-x:hover { color: var(--danger); }
</style>