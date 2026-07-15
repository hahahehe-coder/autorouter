<script lang="ts">
  import LiveTest from '../lib/LiveTest.svelte';
  import ModelSelect from '../lib/ModelSelect.svelte';
  import type { ConfigSnapshot, StrategyData } from '../api';
  import { selVal } from '../lib/dom';

  export let snapshot: ConfigSnapshot;
  export let dirty = false;
  export let models: string[] = [];   // 注册表里的模型名(给 ModelSelect 用)

  export let onChange: () => void = () => {};

  let showAddStrategy = false;
  let newStrategyName = '';

  // 注册表为空时不允许新增策略(rule.model 必须从注册表选)
  $: registryEmpty = models.length === 0;
  // 默认 rule.model 用注册表首个(避免出现空 model)
  $: defaultModel = models[0] ?? '';

  // classifier 输出下标 → 人类标签(给 UI 显示用)
  const CLASSIFIER_LABELS = ['0 trivial', '1 medium', '2 code', '3 heavy'];

  function commitNewStrategy() {
    const name = newStrategyName.trim();
    if (!name || snapshot.strategies[name]) {
      if (snapshot.strategies[name]) alert('策略名已存在');
      return;
    }
    if (registryEmpty) {
      alert('注册表为空,请先在「模型」tab 拉取/添加模型');
      return;
    }
    const rule = { model: defaultModel };
    snapshot.strategies[name] = {
      kind: 'single',
      rule,
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
  function changeKind(name: string, kind: 'single' | 'rule' | 'classifier') {
    const s = snapshot.strategies[name];
    s.kind = kind;                                   // 必须写,模板靠它切分支
    if (kind === 'single') {
      if (!s.rule || !s.rule.model) s.rule = { model: defaultModel };
      delete s.rules;
    } else {
      s.rules = (s.rules && s.rules.length >= 2) ? s.rules : [{ model: defaultModel }, { model: defaultModel }];
      delete s.rule;
    }
    snapshot = snapshot; onChange();
  }
  function onKindSelect(name: string) {
    return (e: Event) => changeKind(name, selVal(e) as 'single' | 'rule' | 'classifier');
  }

  // --- static rule ---
  function setStaticModel(name: string, e: Event) {
    if (!snapshot.strategies[name].rule) snapshot.strategies[name].rule = { model: '' };
    snapshot.strategies[name].rule!.model = (e as CustomEvent).detail;
    snapshot = snapshot; onChange();
  }
  function setStaticField(name: string, field: 'max_tokens' | 'system' | 'thinking', val: any) {
    if (!snapshot.strategies[name].rule) snapshot.strategies[name].rule = { model: '' };
    const r = snapshot.strategies[name].rule!;
    if (val === '' || val === null || val === undefined) {
      delete r[field];
    } else {
      r[field] = val;
    }
    snapshot = snapshot; onChange();
  }
  function onStaticMaxTokensInput(name: string, e: Event) {
    const v = parseInt((e.target as HTMLInputElement).value);
    setStaticField(name, 'max_tokens', Number.isNaN(v) ? '' : v);
  }
  function onStaticSystemInput(name: string, e: Event) {
    setStaticField(name, 'system', (e.target as HTMLTextAreaElement).value);
  }
  function onStaticThinkingInput(name: string, e: Event) {
    setStaticField(name, 'thinking', (e.target as HTMLInputElement).value);
  }

  // --- heuristic rules ---
  function onRuleModelInput(name: string, idx: number, e: Event) {
    snapshot.strategies[name].rules![idx].model = (e as CustomEvent).detail;
    snapshot = snapshot; onChange();
  }
  function onRuleFieldInput(name: string, idx: number, field: 'max_tokens' | 'system' | 'thinking', val: any) {
    const r = snapshot.strategies[name].rules![idx];
    if (val === '' || val === null || val === undefined) {
      delete r[field];
    } else {
      r[field] = val;
    }
    snapshot = snapshot; onChange();
  }
  function onRuleMaxTokensInput(name: string, idx: number, e: Event) {
    const v = parseInt((e.target as HTMLInputElement).value);
    onRuleFieldInput(name, idx, 'max_tokens', Number.isNaN(v) ? '' : v);
  }
  function onRuleSystemInput(name: string, idx: number, e: Event) {
    onRuleFieldInput(name, idx, 'system', (e.target as HTMLTextAreaElement).value);
  }
  function onRuleThinkingInput(name: string, idx: number, e: Event) {
    onRuleFieldInput(name, idx, 'thinking', (e.target as HTMLInputElement).value);
  }
  function addRule(name: string) {
    if (registryEmpty) return;
    snapshot.strategies[name].rules!.push({ model: defaultModel });
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
  <p>每个策略对应上游后台 channel "模型"字段里的一个名字。用户请求 model=&lt;名&gt; 时走对应规则。</p>
</div>

<LiveTest availableStrategies={names} />

<div class="card" style="margin-top: 16px;">
  <div class="card-head">
    <h2>策略列表 <span class="meta">{names.length} 个</span></h2>
    {#if !showAddStrategy}
      <button class="btn btn-secondary" on:click={() => showAddStrategy = true}
        disabled={registryEmpty} title={registryEmpty ? '请先在「模型」tab 注册模型' : ''}>
        + 新增策略
      </button>
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
              <option value="single"     selected={snapshot.strategies[n].kind === 'single'}>single — 单模型</option>
              <option value="rule"       selected={snapshot.strategies[n].kind === 'rule'}>rule — 基于规则(启发式 band)</option>
              <option value="classifier" selected={snapshot.strategies[n].kind === 'classifier'}>classifier — 基于分类器(ML;不可用回退 rule)</option>
            </select>
          </div>
          <div></div>
          <div>
            <button class="btn btn-danger" on:click={() => remove(n)}>删除</button>
          </div>
        </div>

        {#if snapshot.strategies[n].kind === 'single'}
          <div class="field">
            <label class="field-label">模型</label>
            <ModelSelect value={snapshot.strategies[n].rule?.model ?? ''} {models}
              on:change={(e) => setStaticModel(n, e)} />
          </div>
          <div class="field-row">
          <div class="field">
            <label class="field-label">最大输出 token</label>
            <input class="mono" type="number" min="1" value={snapshot.strategies[n].rule?.max_tokens ?? ''}
              placeholder="留空则不覆盖" on:change={(e) => onStaticMaxTokensInput(n, e)} />
          </div>
          <div class="field">
            <label class="field-label">思考强度</label>
            <select class="mono" value={snapshot.strategies[n].rule?.thinking ?? ''}
              on:change={(e) => onStaticThinkingInput(n, e)}>
              <option value="">原样透传</option>
              <option value="off">不思考</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </select>
          </div>
        </div>
        <div class="field">
          <label class="field-label">系统提示</label>
          <textarea class="mono" rows="3" style="resize: none; overflow-y: auto;"
            placeholder="留空则不覆盖;chat 端点注入为 messages[0] system"
            value={snapshot.strategies[n].rule?.system ?? ''}
            on:change={(e) => onStaticSystemInput(n, e)}></textarea>
        </div>
        <p class="muted" style="margin: 4px 0 0; font-size: 12px;">
          端点自动映射:chat → reasoning_effort / messages → thinking + output_config / responses → reasoning。
        </p>
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
                <ModelSelect value={r.model ?? ''} {models} on:change={(e) => onRuleModelInput(n, i, e)} />
              </div>

              <div class="field-row">
                <div class="field">
                  <label class="field-label">最大输出 token</label>
                  <input class="mono" type="number" min="1" value={r.max_tokens ?? ''}
                    placeholder="留空则不覆盖" on:change={(e) => onRuleMaxTokensInput(n, i, e)} />
                </div>
                <div class="field">
                  <label class="field-label">思考强度</label>
                  <select class="mono" value={r.thinking ?? ''}
                    on:change={(e) => onRuleThinkingInput(n, i, e)}>
                    <option value="">原样透传</option>
                    <option value="off">不思考</option>
                    <option value="low">low</option>
                    <option value="medium">medium</option>
                    <option value="high">high</option>
                  </select>
                </div>
              </div>
              <div class="field">
                <label class="field-label">系统提示</label>
                <textarea class="mono" rows="2" style="resize: none; overflow-y: auto;"
                  placeholder="留空则不覆盖" value={r.system ?? ''}
                  on:change={(e) => onRuleSystemInput(n, i, e)}></textarea>
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
  .field-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px; }

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