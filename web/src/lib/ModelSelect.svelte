<script lang="ts">
  /*
    模型选择器 — 严格锁定到「模型配置」页注册表里的模型:
    - 注册表为空:提示去「模型」tab 拉取/添加
    - 只能从下拉里选(无自定义输入、无空选项)
    - 当前 value 不在注册表(被删除/未注册):标红 + 提示
    父组件:models= 传注册表模型名数组,on:change={(e)=>...} 接收新值(e.detail)。
  */
  import { createEventDispatcher } from 'svelte';
  export let value: string = '';
  export let models: string[] = [];
  const dispatch = createEventDispatcher();

  let isInRegistry = (m: string) => m !== '' && models.includes(m);
  $: valid = isInRegistry(value);

  function onSelect(e: Event) {
    const v = (e.target as HTMLSelectElement).value;
    if (!v || v === value) return;
    value = v;
    dispatch('change', v);
  }
</script>

{#if models.length === 0}
  <span class="hint-warn">注册表为空 — 先去「模型」tab 拉取或添加模型</span>
{:else}
  <div class="wrap">
    <select class="mono" class:bad={value !== '' && !valid}
      value={valid ? value : ''}
      on:change={onSelect}>
      {#if !valid}
        <option value="" disabled selected>— 请选择模型 —</option>
      {/if}
      {#each models as m (m)}
        <option value={m} selected={m === value}>{m}</option>
      {/each}
    </select>
    {#if value !== '' && !valid}
      <span class="bad-hint">未注册,请重选</span>
    {/if}
  </div>
{/if}

<style>
  select.mono { font-family: var(--font-mono); font-size: 13px; min-width: 200px; }
  select.mono.bad { border-color: var(--danger); background: var(--danger-soft); }
  .wrap { display: inline-flex; align-items: center; gap: 8px; }
  .bad-hint { color: var(--danger); font-size: 12px; }
  .hint-warn { color: var(--ink-3); font-size: 12px; font-style: italic; }
</style>