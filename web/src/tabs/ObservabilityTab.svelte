<script lang="ts">
  /*
    日志查看 tab(原"观测")。仅配 log_dir;点左边文件,右边看尾部 500 行。
    不做实时刷新 —— 用户手动点"刷新"或重新点文件就好。
  */
  import { onMount } from 'svelte';
  import { api, type ConfigSnapshot, type LogFile, type LogContent } from '../api';

  export let snapshot: ConfigSnapshot;
  export let onChange: () => void = () => {};

  let logDirInput = snapshot.observability?.log_dir ?? './log';
  let files: LogFile[] = [];
  let logDirDisplay = '';
  let selected: string | null = null;
  let content: LogContent | null = null;
  let loadingList = false;
  let loadingContent = false;
  let listError = '';
  let contentError = '';
  let savingDir = false;
  let dirSaveOk = '';

  $: logDirDisplay = snapshot.observability?.log_dir ?? '';

  onMount(refreshList);

  async function refreshList() {
    loadingList = true; listError = '';
    try {
      const r = await api.listLogs();
      files = r.files;
      logDirDisplay = r.log_dir;
    } catch (e: any) {
      listError = e.message;
    } finally {
      loadingList = false;
    }
  }

  async function selectFile(name: string) {
    selected = name;
    loadingContent = true; contentError = ''; content = null;
    try {
      content = await api.readLog(name);
    } catch (e: any) {
      contentError = e.message;
    } finally {
      loadingContent = false;
    }
  }

  async function saveDir() {
    savingDir = true; dirSaveOk = '';
    try {
      await api.putSection('observability', { log_dir: logDirInput });
      snapshot.observability.log_dir = logDirInput;
      onChange();
      dirSaveOk = '已保存';
      await refreshList();
    } catch (e: any) {
      dirSaveOk = '';
      listError = '保存失败: ' + e.message;
    } finally {
      savingDir = false;
    }
  }

  function fmtSize(b: number): string {
    if (b < 1024) return `${b} B`;
    if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
    return `${(b / 1024 / 1024).toFixed(1)} MB`;
  }
  function fmtTime(t: number): string {
    return new Date(t * 1000).toLocaleString();
  }
</script>

<div class="page-head">
  <h1>日志</h1>
  <p>滚动日志查看器(midnight 自动切文件,保留 14 天)。点左边文件名右边看尾部内容。</p>
</div>

<!-- log_dir 配置 -->
<div class="card" style="margin-bottom: 16px;">
  <div class="card-head-line">日志目录</div>
  <div style="display: flex; gap: 8px; align-items: center;">
    <input class="mono" type="text" bind:value={logDirInput} placeholder="./log" style="flex: 1;"
      on:keydown={(e) => { if (e.key === 'Enter') saveDir(); }} />
    <button class="btn btn-primary" on:click={saveDir} disabled={savingDir}>
      {savingDir ? '保存中…' : '保存'}
    </button>
  </div>
  {#if dirSaveOk}<span class="muted" style="margin-left: 8px; color: var(--accent);">{dirSaveOk}</span>{/if}
  {#if logDirDisplay}
    <p class="muted" style="margin: 8px 0 0;">当前目录: <code class="mono">{logDirDisplay}</code></p>
  {/if}
</div>

<!-- 文件列表 + 内容 -->
<div class="card">
  <div class="card-head-line">
    文件列表 <span class="meta">{files.length} 个</span>
    <button class="btn-ghost btn-sm" style="margin-left: auto;"
      on:click={refreshList} disabled={loadingList}>
      {loadingList ? '刷新中…' : '刷新列表'}
    </button>
  </div>

  {#if listError}
    <p class="muted" style="color: var(--danger);">{listError}</p>
  {/if}

  {#if !loadingList && files.length === 0 && !listError}
    <p class="muted">目录里没文件。先发请求或触发路由动作,日志会立刻写到这里。</p>
  {:else}
    <div class="log-pane">
      <div class="log-list">
        {#each files as f (f.name)}
          <button class="log-item" class:active={selected === f.name}
            type="button"
            on:click={() => selectFile(f.name)}>
            <div style="display: flex; align-items: center; gap: 6px; width: 100%;">
              <span class="fname mono">{f.name}</span>
              {#if f.is_today}<span class="badge">今天</span>{/if}
            </div>
            <span class="fmeta">{fmtSize(f.size)} · {fmtTime(f.mtime)}</span>
          </button>
        {/each}
      </div>
      <div class="log-content-pane">
        {#if !selected}
          <p class="muted" style="padding-top: 60px; text-align: center;">← 左边选个文件查看</p>
        {:else if loadingContent}
          <p class="muted">加载中...</p>
        {:else if contentError}
          <p class="muted" style="color: var(--danger);">{contentError}</p>
        {:else if content}
          <pre class="log-pre">{content.content}</pre>
          <p class="muted" style="margin: 8px 0 0;">
            {content.name} · 共 {content.total_lines} 行 · 全量显示
          </p>
        {/if}
      </div>
    </div>
  {/if}
</div>

<style>
  .card-head-line {
    font-size: 15px; font-weight: 600; margin-bottom: 12px;
    display: flex; align-items: center; gap: 8px;
  }
  .meta { color: var(--ink-3); font-weight: normal; font-size: 13px; }
  .log-pane {
    display: grid;
    grid-template-columns: 320px 1fr;
    gap: 16px;
    min-height: 400px;
  }
  .log-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
    max-height: 600px;
    overflow-y: auto;
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 4px;
  }
  .log-item {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 2px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 8px 10px;
    cursor: pointer;
    text-align: left;
    font-family: inherit;
    color: var(--ink);
  }
  .log-item:hover { background: var(--bg); border-color: var(--line); }
  .log-item.active { background: var(--accent-soft); border-color: var(--accent); }
  .fname { font-size: 13px; }
  .fmeta { font-size: 11px; color: var(--ink-3); }
  .badge {
    background: var(--accent);
    color: white;
    font-size: 10px;
    padding: 1px 5px;
    border-radius: 3px;
  }
  .log-content-pane {
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 12px;
    overflow: auto;
    max-height: 600px;
  }
  .log-pre {
    margin: 0; padding: 0;
    font-family: var(--font-mono);
    font-size: 12px;
    white-space: pre-wrap;
    word-break: break-all;
    color: var(--ink);
    background: transparent;
    line-height: 1.4;
  }
</style>