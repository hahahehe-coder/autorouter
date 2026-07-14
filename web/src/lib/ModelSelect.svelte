<script lang="ts">
  /*
    模型选择器。
    - 上游模型已加载: <select> 下拉,带"自定义..."选项
    - 选自定义: 弹文本输入
    - 上游模型未加载: 直接文本输入
    父组件:value= 传当前值,on:change={(e)=>...} 接收新值(e.detail 是模型名)。
  */
  import { createEventDispatcher } from 'svelte';
  export let value: string = '';
  export let upstreamModels: string[] = [];
  const dispatch = createEventDispatcher();

  let customMode = false;
  let customText = '';
  let isInUpstream = (m: string) => upstreamModels.includes(m);

  // 初次 + 每次 upstreamModels 变化时:如果 value 不在列表里,自动切自定义
  $: {
    if (upstreamModels.length > 0 && value && !isInUpstream(value)) {
      customMode = true;
      customText = value;
    }
    if (upstreamModels.length === 0) {
      customMode = false;   // 没上游模型就纯文本输入,别卡自定义模式
    }
  }

  function onSelect(e: Event) {
    const v = (e.target as HTMLSelectElement).value;
    if (v === '__custom__') {
      customMode = true;
      customText = value;
      queueMicrotask(focusCustomInput);
    } else {
      customMode = false;
      value = v;
      dispatch('change', v);
    }
  }

  function onCustomInput(e: Event) {
    customText = (e.target as HTMLInputElement).value;
    value = customText;
    dispatch('change', customText);
  }

  function backToSelect() {
    customMode = false;
    customText = '';
    value = '';
    dispatch('change', '');
  }

  function onPlainInput(e: Event) {
    value = (e.target as HTMLInputElement).value;
    dispatch('change', value);
  }

  function focusCustomInput() {
    const el = document.querySelector('.model-custom-input') as HTMLInputElement | null;
    if (el) { el.focus(); el.select(); }
  }

  const uid = Math.random().toString(36).slice(2, 9);
</script>

{#if upstreamModels.length === 0}
  <input class="mono" type="text" {value} placeholder="未拉取上游(点顶栏按钮)" on:input={onPlainInput} />
{:else if !customMode}
  <select class="mono" {value} on:change={onSelect}>
    <option value="">— 选模型 —</option>
    {#each upstreamModels as m}
      <option value={m}>{m}</option>
    {/each}
    <option value="__custom__">自定义…</option>
  </select>
{:else}
  <div style="display: flex; gap: 6px;">
    <input class="mono model-custom-input" type="text" bind:value={customText} placeholder="输入模型名" on:input={onCustomInput} />
    <button class="btn-x" on:click={backToSelect} title="回到下拉">×</button>
  </div>
{/if}

<style>
  select.mono, input.mono { font-family: var(--font-mono); font-size: 13px; }
  .btn-x {
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 6px;
    cursor: pointer;
    color: var(--ink-3);
    padding: 0 10px;
    font-size: 14px;
  }
  .btn-x:hover { color: var(--danger); border-color: var(--danger-soft); }
</style>