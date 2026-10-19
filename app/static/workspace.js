/* Rednote data operations: publish records, analytics, comments, alerts, retrospectives.
   The creative pipeline is driven by the chat ("新建任务") and shown in the work panel. */
(() => {
  const WS = 'default';
  const BASE = '/api/v1/workspaces/' + WS + '/apps/rednote';
  const $ = (id) => document.getElementById(id);
  const state = { section: 'publish' };

  function setStatus(text, kind) {
    const node = $('ws-status');
    node.textContent = text;
    node.style.color = kind === 'error' ? 'var(--danger)' : (kind === 'ok' ? 'var(--success)' : '');
  }

  async function api(path) {
    const response = await fetch(BASE + path, { headers: { 'X-Workspace-Id': WS } });
    let data = null;
    try { data = await response.json(); } catch (error) { data = null; }
    if (!response.ok) {
      const message = (data && (data.message || (data.detail && data.detail.message))) || ('请求失败 ' + response.status);
      throw new Error(message);
    }
    return data;
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text == null ? '' : String(text);
    return div.innerHTML;
  }

  function card(title, body, note) {
    return '<div class="ws-card"><h3>' + escapeHtml(title) + '</h3>' + body +
      (note ? '<p class="hint">' + escapeHtml(note) + '</p>' : '') + '</div>';
  }

  function empty(icon, title, text) {
    return '<div class="empty-state"><div class="empty-icon">' + icon + '</div><h3>' + escapeHtml(title) +
      '</h3><p>' + escapeHtml(text) + '</p></div>';
  }

  async function renderPublish() {
    const box = $('ws-content');
    box.innerHTML = card('发布记录', '<p class="hint">正在读取…</p>');
    try {
      const data = await api('/publish-plans');
      const plans = data.plans || [];
      const notes = data.notes || [];
      if (!plans.length) {
        box.innerHTML = empty('▤', '还没有发布记录', '在对话里走完创作与审核后，人工发布计划会出现在这里。');
        return;
      }
      box.innerHTML = card('发布计划', plans.map(row =>
        '<div class="ws-row"><strong>' + escapeHtml(row.mode) + ' · ' + escapeHtml(row.status) + '</strong>' +
        '<span class="hint">' + escapeHtml(row.account_ref || '未绑定账号') + ' · ' +
        escapeHtml(row.created_at) + '</span></div>').join(''), data.note) +
        (notes.length ? card('已回填笔记', notes.map(row =>
          '<div class="ws-row"><a href="' + escapeHtml(row.url) + '" target="_blank" rel="noreferrer">' +
          escapeHtml(row.url) + '</a><span class="hint">' + escapeHtml(row.publish_status) + ' · 可见性 ' +
          escapeHtml(row.visibility_status) + '</span></div>').join('')) : '');
    } catch (error) {
      box.innerHTML = card('发布记录', '<p class="hint">' + escapeHtml(error.message) + '</p>');
    }
  }

  async function renderAnalytics() {
    const box = $('ws-content');
    box.innerHTML = card('运营监控', '<p class="hint">正在读取数据合同…</p>');
    try {
      const data = await api('/analytics/overview');
      box.innerHTML = card('运营监控', '<pre>' + escapeHtml(JSON.stringify(data, null, 2)) + '</pre>', data.note);
    } catch (error) {
      box.innerHTML = card('运营监控', '<p class="hint">' + escapeHtml(error.message) + '</p>');
    }
  }

  async function renderComments() {
    const box = $('ws-content');
    try {
      const data = await api('/comments');
      box.innerHTML = card('评论待办', '<p>当前评论样本：' + (data.comments || []).length + '</p>', data.note);
    } catch (error) {
      box.innerHTML = card('评论待办', '<p class="hint">' + escapeHtml(error.message) + '</p>');
    }
  }

  async function renderAlerts() {
    const box = $('ws-content');
    try {
      const data = await api('/alerts');
      box.innerHTML = card('告警', '<p>当前告警：' + (data.alerts || []).length + '</p>', data.note);
    } catch (error) {
      box.innerHTML = card('告警', '<p class="hint">' + escapeHtml(error.message) + '</p>');
    }
  }

  async function renderRetro() {
    const box = $('ws-content');
    try {
      const data = await api('/retrospectives');
      box.innerHTML = card('复盘与经验', '<p>复盘：' + (data.retrospectives || []).length +
        ' · 经验：' + (data.playbooks || []).length + '</p>', data.note);
    } catch (error) {
      box.innerHTML = card('复盘与经验', '<p class="hint">' + escapeHtml(error.message) + '</p>');
    }
  }

  function render() {
    $('ws-nav').querySelectorAll('button[data-ws]').forEach(button => {
      button.classList.toggle('active', button.dataset.ws === state.section);
    });
    const renderers = { publish: renderPublish, analytics: renderAnalytics,
                        comments: renderComments, alerts: renderAlerts, retro: renderRetro };
    (renderers[state.section] || renderPublish)();
  }

  $('ws-nav').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-ws]');
    if (!button || button.disabled) return;
    state.section = button.dataset.ws;
    render();
  });
  $('ws-back').addEventListener('click', () => window.AgentApp.switchView('chat'));

  window.WorkspaceApp = { open: render };
})();
