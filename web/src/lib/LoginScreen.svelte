<script lang="ts">
  /*
    后台登录(只挡 /api/* 设置端点,/v1/* 转发不限)
    通过 setAuth() 把 base64(user:pass) 存到 localStorage,
    之后所有 api.* 调用自动带 Authorization: Basic 头。
    后端中间件:password 空 → 不挡;非空 → 需要 Basic Auth。
  */
  import { setAuth, clearAuth, getAuthB64, onAuthChange } from '../api';

  let user = '';
  let password = '';
  let error = '';
  let submitting = false;
  // 登录提示:由 App.svelte 在检测到 401 时设置一次
  export let promptMessage = '';
  // 登录成功后通知 App.svelte 重新加载 snapshot + 切到 tabs 视图
  export let onLogin: () => void = () => {};

  $: enabled = !!getAuthB64();
  onAuthChange(() => { enabled = !!getAuthB64(); });

  async function submit(e?: Event) {
    e?.preventDefault();
    if (!user || !password) { error = '用户名和密码都要填'; return; }
    submitting = true; error = '';
    setAuth(user, password);
    // 触发一个 dummy 调用验证凭证,不通过就回滚
    try {
      const r = await fetch('/api/config', { headers: { 'Authorization': `Basic ${getAuthB64()}` } });
      if (r.status === 401) {
        clearAuth();
        error = '用户名或密码错误';
      } else if (!r.ok) {
        clearAuth();
        error = `登录失败 (HTTP ${r.status})`;
      } else {
        error = '';
        onLogin();   // 通知父组件刷新 snapshot + 切到 tabs 视图
      }
    } catch (e: any) {
      error = `网络错误: ${e?.message || e}`;
    } finally {
      submitting = false;
    }
  }
</script>

<div class="login-mask">
  <form class="login-card" on:submit={submit}>
    <div class="login-logo">A</div>
    <h2>AutoRouter 后台登录</h2>
    <p class="muted" style="margin: 0 0 16px; font-size: 13px;">
      仅挡 <code>/api/*</code> 设置端点;<code>/v1/*</code> 转发不受影响,可继续接客户端请求。
    </p>
    {#if promptMessage}
      <div class="hint">{promptMessage}</div>
    {/if}

    <div class="field">
      <label class="field-label">用户名</label>
      <input class="mono" type="text" bind:value={user} autocomplete="username"
        disabled={submitting} on:keydown={(e) => { if (e.key === 'Enter') submit(); }} />
    </div>

    <div class="field">
      <label class="field-label">密码</label>
      <input class="mono" type="password" bind:value={password} autocomplete="current-password"
        disabled={submitting} on:keydown={(e) => { if (e.key === 'Enter') submit(); }} />
    </div>

    {#if error}
      <div class="err">{error}</div>
    {/if}

    <button type="submit" class="btn btn-primary" disabled={submitting} style="width: 100%; margin-top: 8px;">
      {submitting ? '登录中…' : '登录'}
    </button>
  </form>
</div>

<style>
  .login-mask {
    position: fixed; inset: 0;
    display: flex; align-items: center; justify-content: center;
    background: var(--bg, #0e1014);
    z-index: 1000;
  }
  .login-card {
    background: var(--card, #1a1c20);
    border: 1px solid var(--line, #2a2d33);
    border-radius: 12px;
    padding: 32px 28px 24px;
    width: 360px;
    box-shadow: 0 12px 40px rgba(0,0,0,.4);
  }
  .login-logo {
    width: 48px; height: 48px;
    border-radius: 10px;
    background: var(--accent, #6c8eef);
    color: #fff;
    font-size: 22px; font-weight: 700;
    display: flex; align-items: center; justify-content: center;
    margin: 0 auto 14px;
  }
  .field { margin-bottom: 12px; }
  .field-label { display: block; font-size: 12px; color: var(--ink-3); margin-bottom: 4px; }
  .hint {
    background: var(--accent-soft, #2a3450); color: var(--accent, #6c8eef);
    padding: 8px 12px; border-radius: 6px; font-size: 13px;
    margin-bottom: 12px;
  }
  .err {
    background: var(--danger-soft, #3a2229); color: var(--danger, #e07b8a);
    padding: 8px 12px; border-radius: 6px; font-size: 13px;
    margin-bottom: 8px;
  }
</style>
