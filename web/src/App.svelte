<script lang="ts">
  import { onMount } from 'svelte';
  import { api, type ConfigSnapshot, getAuthB64, clearAuth, onAuth401 } from './api';
  import StrategiesTab from './tabs/StrategiesTab.svelte';
  import PolicyTab from './tabs/PolicyTab.svelte';
  import ObservabilityTab from './tabs/ObservabilityTab.svelte';
  import ConnectionTab from './tabs/ConnectionTab.svelte';
  import MlTab from './tabs/MlTab.svelte';
  import ModelConfigTab from './tabs/ModelConfigTab.svelte';
  import LoginScreen from './lib/LoginScreen.svelte';

  const TABS = [
    { id: 'connection',    label: '连接' },
    { id: 'modelconfig',   label: '模型' },
    { id: 'strategies',    label: '策略' },
    { id: 'ml',            label: 'ML' },
    { id: 'policy',        label: '后处理' },
    { id: 'observability', label: '日志' },
  ] as const;

  let activeTab: typeof TABS[number]['id'] = 'connection';
  let snapshot: ConfigSnapshot | null = null;
  let dirtySections: Set<string> = new Set();
  let saveError: string = '';
  let saveOk: string = '';

  // 登录态:有凭证(snapshot 加载成功)= 看到 tabs,否则显示 LoginScreen
  let loggedIn = false;
  let loginPrompt = '';   // 提示语(401 时由 api.ts 通知触发)
  const unsubAuth = onAuth401(() => {
    loggedIn = false;
    loginPrompt = '凭证失效或未提供,请重新登录';
    snapshot = null;
  });

  $: models = snapshot ? Object.keys(snapshot.models ?? {}).sort() : [];
  $: dirty = dirtySections.size > 0;
  $: strategyCount = snapshot ? Object.keys(snapshot.strategies ?? {}).length : 0;
  // 已登录 + 已加载 snapshot = 看 tabs,否则弹 LoginScreen
  // 后端 admin.enabled 反映 password 是否配了,password 空 → 自动放行
  $: showTabs = loggedIn && !!snapshot;

  onMount(async () => {
    // 启动时看 localStorage 是否有凭证 → 直接复用(SPA 重启不丢)
    if (getAuthB64()) {
      loggedIn = true;
    }
    await loadSnapshot();
  });

  async function loadSnapshot() {
    try {
      snapshot = await api.getAll();
      // 没密码配值时直接进;有密码但凭证对就进;错就 401 触发 LoginScreen
      const enabled = snapshot?.connection?.admin?.enabled;
      if (!enabled) loggedIn = true;
      else if (getAuthB64()) loggedIn = true;
    } catch (e: any) {
      if (/auth/.test(e.message)) {
        loggedIn = false;
        loginPrompt = '需要登录';
      } else {
        saveError = '加载配置失败: ' + e.message;
      }
    }
  }

  function markDirty(section: string) {
    dirtySections = new Set(dirtySections).add(section);
    saveOk = '';
  }

  function onChangeFromTab() { markDirty(activeTab); }

  async function save() {
    if (!snapshot) return;
    saveError = ''; saveOk = '';
    if (dirtySections.has('connection'))    await api.putSection('connection',    snapshot.connection);
    if (dirtySections.has('policy'))        await api.putSection('policy',        snapshot.policy);
    if (dirtySections.has('strategies'))    await api.putSection('strategies',    snapshot.strategies);
    if (dirtySections.has('observability')) await api.putSection('observability', snapshot.observability);
    if (dirtySections.has('ml'))            await api.putSection('ml',            snapshot.ml);
    if (dirtySections.has('modelconfig'))   await api.putSection('models',        snapshot.models);
    try { await api.reload(); } catch {}
    dirtySections = new Set();
    saveOk = '已保存并立即生效';
    snapshot = await api.getAll();
  }

  async function reloadFromDisk() {
    dirtySections = new Set();
    snapshot = await api.getAll();
    saveOk = '已从磁盘重载';
    saveError = '';
  }

  function logout() {
    clearAuth();
    snapshot = null;
    dirtySections = new Set();
    loggedIn = false;
    loginPrompt = '已退出, 请重新登录';
  }

  // LoginScreen 登录成功后调用:加载 snapshot + 切到 tabs
  async function handleLogin() {
    loggedIn = true;
    await loadSnapshot();
  }
</script>

<div class="app">
  <aside class="sidebar">
    <div class="brand">
      <div class="brand-mark">A</div>
      <div class="brand-name">AutoRouter</div>
    </div>

    <div class="nav-label">配置</div>
    <nav class="nav">
      {#each TABS as tab}
        <div class="nav-item {activeTab === tab.id ? 'active' : ''}" on:click={() => activeTab = tab.id}>
          <span>{tab.label}</span>
          {#if tab.id === 'strategies'}
            <span class="badge">{strategyCount}</span>
          {/if}
        </div>
      {/each}
    </nav>
  </aside>

  <main class="main">
    {#if !showTabs}
      <LoginScreen promptMessage={loginPrompt} onLogin={handleLogin} />
    {:else if activeTab === 'connection'}
      <ConnectionTab bind:snapshot={snapshot} onChange={onChangeFromTab} />
    {:else if activeTab === 'strategies'}
      <StrategiesTab bind:snapshot={snapshot} onChange={onChangeFromTab} {models} />
    {:else if activeTab === 'modelconfig'}
      <ModelConfigTab bind:snapshot={snapshot} onChange={onChangeFromTab} />
    {:else if activeTab === 'ml'}
      <MlTab bind:snapshot={snapshot} onChange={onChangeFromTab} />
    {:else if activeTab === 'policy'}
      <PolicyTab bind:snapshot={snapshot} onChange={onChangeFromTab} />
    {:else if activeTab === 'observability'}
      <ObservabilityTab bind:snapshot={snapshot} onChange={onChangeFromTab} />
    {/if}

    {#if showTabs}
      <div class="toolbar">
        <div class="dirty-indicator {dirty ? 'dirty' : ''}">
          <span class="dot"></span>
          <span>{dirty ? '修改未保存' : '已保存'}</span>
        </div>
        {#if saveOk}<span class="muted">{saveOk}</span>{/if}
        {#if saveError}<span class="muted" style="color: var(--danger);">{saveError}</span>{/if}
        <div class="spacer"></div>
        <button class="btn btn-secondary" on:click={reloadFromDisk}>从磁盘重载</button>
        <button class="btn btn-primary" on:click={save} disabled={!dirty}>保存并热重载</button>
        <button class="btn-ghost btn-sm" on:click={logout} style="margin-left: 4px;">退出</button>
      </div>
    {/if}
  </main>
</div>
