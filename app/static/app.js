(() => {
  const $ = (id) => document.getElementById(id);
  const chatLog = $('chat-log');
  const form = $('composer');
  const input = $('input');
  const sendBtn = $('send');
  const quickActions = $('quick-actions');
  const optionsWrap = $('options-wrap');
  // 五步指示器已按用户要求删除，它的位置给了板块的小选项卡
  const stepper = $('stepper');

  // 板块与交付面（UI 重构后工作栏已删除，这里是新的导航状态）
  const SECTION_TABS = {
    // 对话的子选项卡就是标题栏中间那四个交付面，所以这里留空，避免重复渲染
    chat: [],
    hot: [['xiaohongshu', '小红书'], ['wechat_article', '公众号'],
          ['moments_personal', '朋友圈·个人'], ['moments_store', '朋友圈·门店'],
          ['store_poster', '门店海报']],
    studio: [],
  };
  const SURFACE_LABELS = { xiaohongshu: '小红书', wechat_article: '公众号',
                           moments: '朋友圈', personal_moments: '朋友圈·个人',
                           store_moments: '朋友圈·门店', store_poster: '门店海报' };

  // 元素可能已随重构删除；绑定前判空，避免整页脚本挂掉。
  function on(id, event, handler) {
    const node = $(id);
    if (node) node.addEventListener(event, handler);
  }

  let sessionId = null;
  let lastResult = null;
  let selectedPlatform = 'xiaohongshu';
  // 热点榜单的场景视图（方案里的五个视图），由板块的小选项卡切换
  let hotScenarioView = 'xiaohongshu';
  // 任务批量删除：勾选多个任务一次删掉
  let batchMode = false;
  const batchSelected = new Set();
  let selectedEngine = 'llm';
  let selectedModel = null;
  let lastCoverKey = '';
  let lastHotKey = '';
  let lastQuestions = [];
  let loadingNode = null;
  let currentHotTopics = [];
  let hotTopics = [];
  let selectedHotIndex = -1;
  let wizardHotspot = null;
  let optionsRenderedAt = 0;
  let xhsPollTimer = null;
  let xhsTopicsActive = false;
  let turnInFlight = false;
  const SEND_ICON = sendBtn.innerHTML;

  const PLATFORM_NAMES = { xiaohongshu: '小红书', wechat_article: '公众号', moments: '门店朋友圈', poster: '门店海报' };
  const PLATFORM_TABS = {
    xiaohongshu: [['plan', '图文方案'], ['pages', '六页图文'], ['verify', '核验结果'], ['evidence', '证据'], ['sample', '六页样稿']],
    wechat_article: [['plan', '文章方案'], ['pages', '段落结构'], ['verify', '核验结果'], ['evidence', '证据']],
    moments: [['plan', '短文案'], ['pages', '配图'], ['verify', '核验结果'], ['evidence', '证据']],
    poster: [['plan', '海报方案'], ['pages', '版面'], ['verify', '核验结果'], ['evidence', '证据']],
  };
  const ROLE_NAMES = { hook: '封面', scene: '场景', proof: '事实', detail: '细节', decision: '决策', close: '收尾' };
  const FIELD_NAMES = { audience: '客户画像', budget: '预算', scenario: '使用场景', preferences: '偏好', required_software: '必用软件 / 系统', objective: '内容目标' };
  const TASKS_KEY = 'huawei-agent-tasks-v1';
  const WORK_PANEL_KEY = 'huawei-agent-work-panel';
  const SIDEBAR_KEY = 'huawei-agent-sidebar';
  // Keep in sync with the @media (max-width:980px) block in style.css.
  const NARROW_QUERY = '(max-width:980px)';
  const WELCOME_HTML = chatLog.innerHTML;
  let lastUserText = '';

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text == null ? '' : String(text);
    return div.innerHTML;
  }

  function addMessage(role, html) {
    const div = document.createElement('div');
    div.className = 'msg ' + role;
    div.innerHTML = html;
    chatLog.appendChild(div);
    chatLog.scrollTop = chatLog.scrollHeight;
    return div;
  }

  function toast(text) {
    const node = document.createElement('div');
    node.className = 'toast';
    node.textContent = text;
    document.body.appendChild(node);
    setTimeout(() => node.remove(), 2600);
  }

  let taskCache = [];
  let taskMenuId = null;

  function readLocalTasks() {
    try {
      const stored = JSON.parse(localStorage.getItem(TASKS_KEY) || '[]');
      return Array.isArray(stored) ? stored.map(item => ({
        conversation_id: item.id, title: item.title, updated_ts: (item.updated || 0) / 1000,
        pinned: false,
      })) : [];
    } catch (error) {
      return [];
    }
  }

  async function loadTasks() {
    try {
      const response = await fetch('/api/v2/conversations?limit=50');
      if (!response.ok) throw new Error('task-list');
      const data = await response.json();
      taskCache = Array.isArray(data.conversations) ? data.conversations : [];
    } catch (error) {
      taskCache = readLocalTasks();
    }
    renderTasks();
  }

  function relativeTime(value) {
    const seconds = Math.max(0, Math.round((Date.now() - value) / 1000));
    if (seconds < 60) return '刚刚';
    if (seconds < 3600) return Math.round(seconds / 60) + ' 分钟前';
    if (seconds < 86400) return Math.round(seconds / 3600) + ' 小时前';
    return Math.round(seconds / 86400) + ' 天前';
  }

  function renderTasks() {
    const wrap = $('task-list');
    if (!taskCache.length) {
      wrap.innerHTML = '<p class="hint">暂无任务</p>';
      return;
    }
    wrap.innerHTML = taskCache.map(task => {
      const raw = task.conversation_id || '';
      const id = escapeHtml(raw);
      const pinned = task.pinned ? '<span class="task-pin" title="已置顶">📌</span> ' : '';
      const updated = task.updated_ts ? relativeTime(task.updated_ts * 1000) : '';
      if (batchMode) {
        const checked = batchSelected.has(raw) ? ' checked' : '';
        return '<div class="task-item batch">' +
          '<label class="task-check"><input type="checkbox" data-check="' + id + '"' + checked + '>' +
          '<span class="task-title">' + pinned + escapeHtml(task.title || '新任务') + '</span>' +
          '<span class="task-time">' + updated + '</span></label>' +
          '</div>';
      }
      return '<div class="task-item' + (id === sessionId ? ' active' : '') + '">' +
        '<button type="button" class="task-main" data-open="' + id + '">' +
        '<span class="task-title">' + pinned + escapeHtml(task.title || '新任务') + '</span>' +
        '<span class="task-time">' + updated + '</span></button>' +
        '<button type="button" class="task-more" data-menu="' + id + '" aria-label="任务菜单">⋯</button>' +
        '</div>';
    }).join('');
    syncBatchBar();
  }

  function syncBatchBar() {
    const bar = $('task-batch-bar');
    const toggle = $('task-batch-toggle');
    if (bar) bar.hidden = !batchMode;
    if (toggle) {
      toggle.textContent = batchMode ? '完成' : '批量';
      toggle.classList.toggle('active', batchMode);
    }
    const count = $('task-batch-count');
    if (count) count.textContent = '已选 ' + batchSelected.size + ' 项';
    const all = $('task-select-all');
    if (all) {
      all.checked = taskCache.length > 0 && batchSelected.size === taskCache.length;
      all.indeterminate = batchSelected.size > 0 && batchSelected.size < taskCache.length;
    }
  }

  function setBatchMode(on) {
    batchMode = on;
    if (!on) batchSelected.clear();
    renderTasks();
    hideTaskMenu();
  }

  async function deleteSelectedTasks() {
    if (!batchSelected.size) { toast('先选择要删除的任务'); return; }
    const ids = [...batchSelected];
    if (!confirm('删除选中的 ' + ids.length + ' 个任务？该操作不可撤销。')) return;
    const button = $('task-batch-delete');
    if (button) button.disabled = true;
    try {
      const response = await fetch('/api/v2/conversations/batch-delete', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids }),
      });
      const data = await response.json();
      if (!response.ok) { toast(data.message || '删除失败'); return; }
      // 逐条结果如实汇报：缺失与失败都算出来，不合并成一句成功。
      const parts = ['已删除 ' + data.deleted_count + ' 个'];
      if ((data.missing || []).length) parts.push('不存在 ' + data.missing.length + ' 个');
      if ((data.failed || []).length) parts.push('失败 ' + data.failed.length + ' 个');
      toast(parts.join('，'));
      if ((data.deleted || []).includes(sessionId)) {
        sessionId = null;
        applyLockState(null);
        chatLog.innerHTML = '';
      }
      batchSelected.clear();
      await loadTasks();
      renderTasks();
    } catch (error) {
      toast('删除请求失败');
    } finally {
      if (button) button.disabled = false;
    }
  }

  function hideTaskMenu() {
    const menu = $('task-menu');
    menu.hidden = true;
    menu.innerHTML = '';
    taskMenuId = null;
  }

  function showTaskMenu(id, anchor) {
    const task = taskCache.find(item => item.conversation_id === id);
    if (!task) return;
    taskMenuId = id;
    const menu = $('task-menu');
    menu.innerHTML =
      '<button type="button" data-task-action="pin">' + (task.pinned ? '取消置顶' : '置顶任务') + '</button>' +
      '<button type="button" data-task-action="rename">重命名</button>' +
      '<button type="button" class="danger" data-task-action="delete">删除任务</button>';
    menu.hidden = false;
    const rect = anchor.getBoundingClientRect();
    const width = 140;
    const left = Math.max(8, rect.right - width);
    let top = rect.bottom + 4;
    if (top + 132 > window.innerHeight) top = Math.max(8, rect.top - 132);
    menu.style.left = left + 'px';
    menu.style.top = top + 'px';
  }

  async function patchTask(id, body) {
    const response = await fetch('/api/v2/conversations/' + encodeURIComponent(id) + '/update', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    if (!response.ok) {
      toast('操作失败，请重试');
      return false;
    }
    await loadTasks();
    return true;
  }

  async function deleteTask(id) {
    if (!confirm('删除这个任务？删除后无法恢复。')) return;
    const response = await fetch('/api/v2/conversations/' + encodeURIComponent(id) + '/delete', {
      method: 'POST',
    });
    if (!response.ok) {
      toast('删除失败，请重试');
      return;
    }
    if (sessionId === id) newTask();
    await loadTasks();
    toast('任务已删除');
  }

  function newTask() {
    sessionId = null;
    applyLockState(null);
    lastResult = null;
    lastUserText = '';
    selectedHotIndex = -1;
    chatLog.innerHTML = WELCOME_HTML;
    optionsWrap.hidden = true;
    optionsWrap.innerHTML = '';
    quickActions.hidden = true;
    $('candidate-board').hidden = true;
    $('draft-card').hidden = true;
    $('image-results').hidden = true;
    $('image-results').innerHTML = '';
    $('cover-result').hidden = true;
    $('publish-state').textContent = '尚未通过核验，不可发布';
    $('publish-state').className = '';
    hideTaskMenu();
    loadTasks();
    switchView('chat');
    input.value = '';
    sendBtn.disabled = true;
    input.focus();
    toast('已新建任务');
    // On narrow screens the sidebar is an overlay: get it out of the way.
    if (isNarrow()) setSidebar(false);
  }

  async function openTask(id) {
    if (!id) return;
    if (id === sessionId) {
      switchView('chat');
      return;
    }
    try {
      const response = await fetch('/api/v2/conversations/' + encodeURIComponent(id));
      if (!response.ok) {
        toast('任务不存在或已删除');
        await loadTasks();
        return;
      }
      const conversation = await response.json();
      const visible = (conversation.messages || []).filter(item =>
        (item.role === 'user' || item.role === 'assistant') &&
        !(item.role === 'assistant' && (!item.content || item.content.startsWith('{"__tool_calls__'))));
      if (!visible.length && !(conversation.artifacts || []).length) {
        toast('这个任务还没有内容');
        return;
      }
      sessionId = id;
      lastResult = null;
      loadSurfaceLock();
      lastUserText = [...visible].reverse().find(item => item.role === 'user')?.content || '';
      chatLog.innerHTML = '';
      visible.forEach(item => addMessage(item.role === 'user' ? 'operator' : 'assistant',
        '<p>' + escapeHtml(item.content || '').replace(/\n/g, '<br>') + '</p>'));
      const task = conversation.task || {};
      // The autonomous runner has no guided "stage"; render whatever the task actually
      // holds instead of gating on stage names only the legacy runner produces.
      if (task.candidates && task.candidates.length) {
        renderV2Candidates(task.candidates);
      } else if (task.plan && !(conversation.artifacts || []).length) {
        renderV2Plan(task.plan, task.selected);
      }
      if ((conversation.artifacts || []).length) renderV2Artifact(conversation);
      if (task.images && task.images.length) renderV2Images(task.images);
      switchView('chat');
      if (isNarrow()) setSidebar(false);
      await loadTasks();
    } catch (error) {
      toast('任务加载失败，请重试');
    }
  }

  function setWorkPanel(open) {
    // 工作栏已按用户要求删除；保留函数是为了不动既有调用点，它现在只记住偏好。
    const panel = $('work-panel');
    if (panel) panel.classList.toggle('collapsed', !open);
    try {
      localStorage.setItem(WORK_PANEL_KEY, open ? '1' : '0');
    } catch (error) {
      // Non-persistent toggle is fine.
    }
  }

  function isNarrow() {
    return window.matchMedia(NARROW_QUERY).matches;
  }

  // The sidebar has two different mechanisms: on wide screens it is a normal column
  // that collapses, on narrow screens it is a fixed overlay driven by body.sidebar-open.
  // Mixing them is what made it possible to open but not close.
  function setSidebar(open) {
    const sidebar = $('sidebar');
    if (isNarrow()) {
      document.body.classList.toggle('sidebar-open', open);
    } else {
      document.body.classList.remove('sidebar-open');
      sidebar.classList.toggle('collapsed', !open);
      try {
        localStorage.setItem(SIDEBAR_KEY, open ? '1' : '0');
      } catch (error) {
        // Non-persistent toggle is fine.
      }
    }
    $('sidebar-toggle').setAttribute('aria-expanded', String(open));
    const collapseBtn = $('sidebar-collapse');
    if (collapseBtn) collapseBtn.setAttribute('aria-expanded', String(open));
  }

  function sidebarIsOpen() {
    return isNarrow() ? document.body.classList.contains('sidebar-open')
      : !$('sidebar').classList.contains('collapsed');
  }

  function hideStarterRow() {
    const row = $('starter-row');
    if (row) row.hidden = true;   // 已经开始对话，示例不再需要占位置
  }

  function setBusy(busy) {
    sendBtn.disabled = busy;
    sendBtn.innerHTML = busy ? '<span class="send-spinner" aria-hidden="true"></span>' : SEND_ICON;
    sendBtn.setAttribute('aria-label', busy ? '生成中' : '发送');
    if (busy) {
      loadingNode = addMessage('assistant', '<p class="loading">Agent 正在调用工具与技能…</p>' +
        '<div class="agent-running">' +
        '<span class="trace-step active">tool · 官方知识库</span>' +
        '<span class="trace-step">skill · 图片核验</span>' +
        '<span class="trace-step">skill · 六页方案</span>' +
        '<span class="trace-step">skill · 合规扫描</span></div>');
    } else if (loadingNode) {
      loadingNode.remove();
      loadingNode = null;
    }
  }

  async function requestTurn(payload) {
    if (turnInFlight) return;
    turnInFlight = true;
    setBusy(true);
    try {
      const response = await fetch('/api/v1/conversations/turn', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      let result = null;
      try { result = await response.json(); } catch (error) { result = null; }
      if (!response.ok) {
        addMessage('assistant', '<p>' + escapeHtml((result && result.message) || '请求失败，请稍后重试') + '</p>');
        return;
      }
      sessionId = result.session_id || sessionId;
      render(result);
    } catch (error) {
      addMessage('assistant', '<p>网络错误，请检查连接后重试。</p>');
    } finally {
      turnInFlight = false;
      setBusy(false);
      sendBtn.disabled = !input.value.trim();
    }
  }

  function agentTraceHtml(trace) {
    if (!trace || !trace.length) return '';
    return '<div class="tool-trace">' + trace.map(step =>
      '<span class="trace-step ' + (step.ok ? 'done' : 'warn') + '">tool · ' + escapeHtml(step.tool) +
      ' · ' + escapeHtml(step.summary || '') + '</span>').join('') + '</div>';
  }

  async function agentTurn(message) {
    if (turnInFlight) return;
    turnInFlight = true;
    setBusy(true);
    try {
      const response = await fetch('/api/v1/workspaces/default/apps/rednote/agent/turn', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Workspace-Id': 'default' },
        body: JSON.stringify({ message: message, conversation_id: sessionId, model: selectedModel }),
      });
      const data = await response.json();
      if (!response.ok) {
        addMessage('assistant', '<p>' + escapeHtml(data.message || 'Agent 调用失败') + '</p>');
        return;
      }
      addMessage('assistant', '<p>' + escapeHtml(data.reply) + '</p>' + agentTraceHtml(data.tool_trace));
    } catch (error) {
      addMessage('assistant', '<p>网络错误，请稍后重试。</p>');
    } finally {
      turnInFlight = false;
      setBusy(false);
      sendBtn.disabled = !input.value.trim();
    }
  }

  function eventTrace(events) {
    const rows = (events || []).filter(item => item.type === 'tool_finished');
    if (!rows.length) return '';
    return '<div class="tool-trace">' + rows.map(item =>
      '<span class="trace-step ' + (item.ok ? 'done' : 'warn') + '">tool · ' +
      escapeHtml(item.tool || '') + ' · ' + escapeHtml(item.summary || '') + '</span>').join('') + '</div>';
  }

  // 去图文工作室：把当前会话的产品与文案带过去，生图与改文案都在那边完成。
  function studioEntryHtml() {
    const id = escapeHtml(sessionId || '');
    return '<div class="studio-entry">'
      + '<span>要配图或继续改文案？</span>'
      + '<a class="studio-link" href="/studio/workbench?conversation=' + id + '"'
      + ' target="_blank" rel="noopener">去图文工作室 →</a>'
      + '<span class="studio-note">产品与文案会一起带过去；生图由你在工作室里手动触发。</span>'
      + '</div>';
  }

  function renderV2Artifact(conversation) {
    const artifacts = (conversation && conversation.artifacts) || [];
    const artifact = artifacts[0];
    if (!artifact || !artifact.content) return;
    $('image-results').hidden = true;
    $('image-results').innerHTML = '';
    const content = artifact.content;
    const card = $('draft-card');
    $('candidate-board').hidden = true;
    card.hidden = false;
    $('draft-eyebrow').textContent = '交付物 · v' + artifact.latest_revision;
    $('draft-title').textContent = content.title || '';
    $('draft-meta').textContent = content.product_id ? '产品证据已绑定' : '创意内容（未绑定产品事实）';
    $('draft-body').textContent = content.body || '';
    $('draft-cta').textContent = content.cta ? '行动引导：' + content.cta : '';
    $('draft-tags').innerHTML = (content.tags || []).map(tag =>
      '<span>#' + escapeHtml(tag) + '</span>').join('');
    const check = artifact.check || {};
    $('draft-state').textContent = check.passed ? '已通过检查' : '有待处理问题';
    $('draft-state').className = 'draft-state' + (check.passed ? ' ready' : '');
    if (sessionId) {
      try { localStorage.setItem('hwpc_conversation', sessionId); } catch (error) { /* 隐私模式忽略 */ }
    }
    const studioHost = $('draft-studio');
    if (studioHost) studioHost.innerHTML = studioEntryHtml();
    const pages = content.pages || [];
    $('pages-empty').hidden = pages.length > 0;
    $('pages-grid').hidden = pages.length === 0;
    $('pages-grid').innerHTML = pages.map((page, index) =>
      '<article class="page-card"><div class="page-card-head"><span class="page-role">' +
      escapeHtml(ROLE_NAMES[page.role] || page.role || '页面') + '</span><span class="page-number">' +
      String(index + 1).padStart(2, '0') + ' / ' + String(pages.length).padStart(2, '0') +
      '</span></div><h4 class="page-title">' + escapeHtml(page.title || '') +
      '</h4><p class="page-text">' + escapeHtml(page.text || '') + '</p>' +
      ((page.fact_ids || []).length ? '<div class="page-facts">' + page.fact_ids.map(id =>
        '<span class="fact-chip">' + escapeHtml(id) + '</span>').join('') + '</div>' : '') +
      '</article>').join('');
    switchTab('plan');
    loadTasks();
  }

  function renderV2Candidates(candidates) {
    const board = $('candidate-board');
    if (!candidates || !candidates.length) { board.hidden = true; return; }
    $('draft-card').hidden = true;
    board.hidden = false;
    board.innerHTML = candidates.map((candidate, index) =>
      '<article class="candidate-card">' +
      '<div class="candidate-head"><span class="candidate-rank">' + (index + 1) + '</span>' +
      '<div class="candidate-name">' + escapeHtml(candidate.model || '') + '</div></div>' +
      '<div class="candidate-meta">' + escapeHtml(candidate.reason || '知识库现有型号') + '</div>' +
      '<div class="candidate-reasons">' + (candidate.facts || []).map(fact =>
        '<span class="fact-chip">' + escapeHtml(fact) + '</span>').join('') + '</div>' +
      '<div class="candidate-actions"><button type="button" class="btn-primary" data-pick="' +
      escapeHtml(candidate.model || '') + '">选这款</button></div></article>').join('');
  }

  function renderV2Plan(plan, selected) {
    if (!plan) return;
    $('candidate-board').hidden = true;
    const card = $('draft-card');
    card.hidden = false;
    $('draft-eyebrow').textContent = '方案确认';
    $('draft-title').textContent = (selected && selected.model) || '内容方案';
    $('draft-meta').textContent = '小红书 · ' + (plan.page_count || 6) + ' 页 · ' +
      (plan.tone || '轻松种草') + ' · ' + (plan.images || '知识库原图');
    $('draft-body').textContent = '确认后开始生成文案，配图直接使用知识库里的产品原图。';
    $('draft-cta').textContent = '';
    $('draft-tags').innerHTML = '';
    $('draft-state').textContent = '待确认';
    $('draft-state').className = 'draft-state';
    $('pages-empty').hidden = false;
    $('pages-grid').hidden = true;
    switchTab('plan');
  }

  function renderV2Images(images) {
    const host = $('image-results');
    if (!images || !images.length) { host.hidden = true; return; }
    host.hidden = false;
    host.innerHTML = images.map(image =>
      '<figure><img src="' + escapeHtml(image.access_url || '') + '" alt="' +
      escapeHtml(image.caption || '知识库产品原图') + '" loading="lazy"><figcaption>' +
      escapeHtml(image.caption || '知识库原图') + '</figcaption></figure>').join('');
  }

  // 交付面建议：先问用户，确认后才切换。UI 自动跳到对应选项卡，底层锁定也跟着改。
  function renderSurfaceSuggestion(messageEl, suggestion) {
    if (!messageEl || !suggestion || !suggestion.surface) return;
    if (suggestion.surface === selectedPlatform
        && $('lock-state') && !$('lock-state').hidden) return;   // 已经是它，不必再问
    const card = document.createElement('div');
    card.className = 'surface-suggest';
    const note = suggestion.unconfirmed && suggestion.open_question
      ? '<span class="suggest-warn">' + escapeHtml(suggestion.open_question) + '</span>' : '';
    card.innerHTML =
      '<span class="suggest-icon">✦</span>' +
      '<div class="suggest-text">' +
      '<strong>这次要做「' + escapeHtml(suggestion.label || suggestion.surface) + '」吗？</strong>' +
      '<span>' + escapeHtml(suggestion.reason || '根据前面的对话判断') + '</span>' + note +
      '</div>' +
      '<div class="suggest-actions">' +
      '<button type="button" class="chip active" data-switch="' +
      escapeHtml(suggestion.surface) + '">切换过去</button>' +
      '<button type="button" class="chip" data-keep="1">保持当前</button>' +
      '</div>';
    messageEl.appendChild(card);
    card.addEventListener('click', async (event) => {
      if (event.target.closest('[data-keep]')) { card.remove(); return; }
      const button = event.target.closest('[data-switch]');
      if (!button) return;
      const target = button.getAttribute('data-switch');
      card.querySelectorAll('button').forEach((item) => { item.disabled = true; });
      await lockSurface(target);          // 后端锁定
      await loadSurfaceLock();            // 选项卡与状态跟着动
      card.remove();
      addMessage('assistant', '<p class="hint">已切换到「' +
        escapeHtml(surfaceLabel(target)) + '」，接下来的内容按这个交付面写。</p>');
      toast('已切换到' + surfaceLabel(target));
    });
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  function renderV2Actions(messageEl, actions) {
    if (!messageEl || !actions || !actions.length) return;
    const row = document.createElement('div');
    row.className = 'v2-actions';
    row.innerHTML = actions.map(action =>
      '<button type="button" class="chip" data-action="' + escapeHtml(action) + '">' +
      escapeHtml(action) + '</button>').join('');
    messageEl.appendChild(row);
    row.addEventListener('click', event => {
      const button = event.target.closest('[data-action]');
      if (!button) return;
      const text = button.getAttribute('data-action');
      addMessage('operator', '<p>' + escapeHtml(text) + '</p>');
      send(text);
    });
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  async function sendV2(message) {
    if (turnInFlight) return;
    turnInFlight = true;
    setBusy(true);
    if (message && message.trim()) lastUserText = message.trim();
    try {
      if (!sessionId) {
        // No mode is sent: the server owns the default runner version, so the entry
        // point cannot drift from the backend again.
        const created = await fetch('/api/v2/conversations', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        const conversation = await created.json();
        sessionId = conversation.conversation_id;
        applyLockState(null);   // 新会话默认未锁定，避免沿用上一个会话的锁定显示
        loadSurfaceLock();
      }
      const response = await fetch('/api/v2/conversations/' + sessionId + '/messages', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: message || '', model: selectedModel,
                               idempotency_key: (crypto.randomUUID && crypto.randomUUID()) ||
                                 String(Date.now()) + Math.random() }),
      });
      const data = await response.json();
      if (!response.ok) {
        addMessage('assistant', '<p>' + escapeHtml(data.message || '任务执行失败') + '</p>');
        return;
      }
      const messageEl = addMessage('assistant', '<p>' +
        escapeHtml(data.reply || '已完成').replace(/\n/g, '<br>') + '</p>' + eventTrace(data.events));
      renderV2Actions(messageEl, data.actions);
      renderSurfaceSuggestion(messageEl, data.surface_suggestion);
      if (data.candidates && data.candidates.length) {
        renderV2Candidates(data.candidates);
      } else {
        const conversation = await fetch('/api/v2/conversations/' + sessionId).then(r => r.json());
        if (conversation.artifacts && conversation.artifacts.length) {
          renderV2Artifact(conversation);
        } else if (data.plan) {
          renderV2Plan(data.plan, data.selected);
        }
      }
      if (data.images) renderV2Images(data.images);
      chatLog.scrollTop = chatLog.scrollHeight;
    } catch (error) {
      addMessage('assistant', '<p>网络错误，请稍后重试。</p>');
    } finally {
      turnInFlight = false;
      setBusy(false);
      sendBtn.disabled = !input.value.trim();
      loadTasks();
    }
  }

  function send(message, slotUpdates) {
    if (turnInFlight) return;
    return sendV2(message || '');
  }

  function render(result) {
    lastResult = result;
    renderChat(result);
    renderOptions(result.questions);
    renderCandidates(result);
    renderDraft(result);
    renderPages(result);
    renderEvidence(result.handoff);
    renderVerify(result);
    renderStepper(result);
    updateQuickActions(result);
    updatePublishState(result);
    updateEngineBadge(result.draft);
    if (result.draft || (result.candidates && result.candidates.length && !result.selected)) {
      switchTab('plan');
    }
    if (result.slots) loadHotspots(result.slots);
    loadTasks();
  }

  function traceHtml(result) {
    const steps = [['skill · 需求解析', 'done']];
    if (result.slots && result.slots.hotspot) steps.push(['skill · 热点接入', 'done']);
    if (result.handoff || (result.candidates && result.candidates.length)) steps.push(['tool · 官方知识库', 'done']);
    if (result.candidates && result.candidates.length) steps.push(['skill · 候选核验', 'done']);
    if (result.selected) steps.push(['gate · 产品确认', 'done']);
    if (result.draft) steps.push(['skill · 六页方案', 'done']);
    if (result.compliance_report) {
      steps.push(['skill · 合规扫描', result.compliance_report.passed ? 'done' : 'warn']);
    }
    if (result.can_publish) steps.push(['gate · 人工确认', 'done']);
    return '<div class="tool-trace">' + steps.map(([label, state]) =>
      '<span class="trace-step ' + state + '">' + escapeHtml(label) + '</span>').join('') + '</div>';
  }

  function renderChat(result) {
    if (result.message) {
      addMessage('assistant', '<p>' + escapeHtml(result.message) + '</p>' + traceHtml(result));
    }
    if (result.status === 'candidates' && result.candidates && result.candidates.length && !result.draft) {
      const names = result.candidates.map((candidate, index) => (index + 1) + '. ' + candidate.model_name).join('；');
      addMessage('assistant', '<p>已找到候选：' + escapeHtml(names) + '</p>' +
        '<p class="hint">在右侧候选卡点击“选 N”继续；也可以在下方向导里选。</p>');
    }
    if (result.issues && result.issues.length) {
      addMessage('assistant', '<ul class="issues">' +
        result.issues.slice(0, 4).map(item => '<li>' + escapeHtml(item) + '</li>').join('') + '</ul>');
    }
  }

  function renderOptions(questions) {
    lastQuestions = questions || [];
    const usable = lastQuestions.filter(question => question.options && question.options.length);
    if (!usable.length) {
      optionsWrap.hidden = true;
      optionsWrap.innerHTML = '';
      return;
    }
    const question = usable[0];
    if (question.field === 'draft_decision') {
      // Publish decisions belong in the compact action row under the composer,
      // not in a long-lived card above it.
      optionsWrap.hidden = true;
      optionsWrap.innerHTML = '';
      return;
    }
    optionsWrap.hidden = false;
    $('ctx-menu').hidden = true;
    const title = question.field === 'candidate_id' ? '产品确认' : '需求确认';
    const options = question.options.map(option => {
      const slot = option.slot_updates ? ' data-slot="' + escapeHtml(JSON.stringify(option.slot_updates)) + '"' : '';
      return '<button type="button" class="wizard-option" data-message="' +
        escapeHtml(option.message || option.label) + '"' + slot + '>' + escapeHtml(option.label) + '</button>';
    }).join('');
    let html = '<div class="wizard" data-field="' + escapeHtml(question.field) + '"><div class="wizard-head"><span>' + title + '</span>' +
      '<span class="hint">' + (usable.length > 1 ? '第 1 / ' + usable.length + ' 问' : '请选择') + '</span></div>' +
      '<div class="wizard-question">' + escapeHtml(question.question) + '</div>' +
      '<div class="wizard-options">' + options + '</div>';
    if (question.field === 'objective') {
      if (!currentHotTopics.length && lastResult && lastResult.slots) {
        loadHotspots(lastResult.slots);
      }
      if (currentHotTopics.length) {
        html += '<div class="wizard-hotspots"><div class="option-label">热点（可选，选中后会结合进推文）</div>' +
          '<div class="chip-row">' + currentHotTopics.slice(0, 3).map(topic =>
            '<button type="button" class="chip' + (wizardHotspot === topic.title ? ' active' : '') +
            '" data-wizard-hotspot="' + escapeHtml(topic.title) + '">' + escapeHtml(topic.title) + '</button>').join('') +
          '</div></div>';
      } else {
        html += '<div class="wizard-hotspots"><div class="option-label">热点（可选）</div>' +
          '<span class="hint">热点加载中，也可以先直接继续。</span></div>';
      }
    }
    html += '<div class="wizard-foot">点击一个选项后自动进入下一题；也可以直接在输入框补充。</div></div>';
    optionsWrap.innerHTML = html;
    optionsRenderedAt = Date.now();
  }

  function renderCandidates(result) {
    const board = $('candidate-board');
    const draftCard = $('draft-card');
    if (result.draft || !result.candidates || !result.candidates.length) {
      if (!result.candidates || !result.candidates.length) board.hidden = true;
      return;
    }
    draftCard.hidden = true;
    board.hidden = false;
    const budgetNames = { not_limited: '不限预算', verified: '预算内已核实', unverified: '价格待核实' };
    board.innerHTML = result.candidates.map((candidate, index) => {
      const selected = result.selected && result.selected.candidate_id === candidate.candidate_id;
      const price = candidate.current_price
        ? '¥' + escapeHtml(candidate.current_price.amount) + ' · ' + escapeHtml(candidate.current_price.configuration)
        : '当前有效价格待核实';
      const reasons = (candidate.reasons || []).slice(0, 3)
        .map(reason => '<span class="fact-chip">' + escapeHtml(reason.dimension) + '</span>').join('');
      const unknowns = (candidate.unknowns || []).slice(0, 3)
        .map(item => '<li>' + escapeHtml(item) + '</li>').join('');
      return '<article class="candidate-card' + (selected ? ' selected' : '') + '">' +
        '<div class="candidate-head"><span class="candidate-rank">' + (index + 1) + '</span>' +
        '<div class="candidate-name">' + escapeHtml(candidate.model_name) + '</div></div>' +
        '<div class="candidate-meta">' + escapeHtml(candidate.eligibility === 'verified' ? '证据完整' : '待核实') +
        '｜预算：' + escapeHtml(budgetNames[candidate.budget_status] || candidate.budget_status || '未核验') + '</div>' +
        '<div class="candidate-price">' + price + '</div>' +
        '<div class="candidate-reasons">' + reasons + '</div>' +
        (unknowns ? '<ul class="candidate-unknowns">' + unknowns + '</ul>' : '') +
        '<div class="candidate-actions"><button type="button" class="btn-primary" data-select="' + (index + 1) + '">' +
        (selected ? '已选，继续' : '选 ' + (index + 1)) + '</button></div></article>';
    }).join('');
  }

  function renderDraft(result) {
    const card = $('draft-card');
    const board = $('candidate-board');
    const draft = result.draft;
    if (!draft) {
      card.hidden = true;
      // 空状态块已按要求删除：没有草稿也没有候选时，对话区保持只有对话本身。
      if (!result.candidates || !result.candidates.length) board.hidden = true;
      return;
    }
    board.hidden = true;
    card.hidden = false;
    const slots = result.slots || {};
    const platform = PLATFORM_NAMES[draft.platform] || draft.platform_name || '小红书';
    $('draft-eyebrow').textContent = platform + ' · 六页图文方案';
    $('draft-title').textContent = draft.title || '';
    $('draft-meta').textContent = [slots.audience, slots.scenario, slots.objective].filter(Boolean).join(' · ');
    $('draft-body').textContent = draft.body || '';
    $('draft-cta').textContent = draft.cta ? '行动引导：' + draft.cta : '';
    $('draft-tags').innerHTML = (draft.tags || []).map(tag => '<span>#' + escapeHtml(tag) + '</span>').join('');
    const state = $('draft-state');
    state.textContent = result.can_publish ? '已确认' : '待人工确认';
    state.className = 'draft-state' + (result.can_publish ? ' ready' : '');
    const images = (result.handoff && result.handoff.images) || [];
    $('draft-images').innerHTML = images.slice(0, 6).map(image =>
      '<img src="' + escapeHtml(image.access_url) + '" alt="' + escapeHtml(image.caption) + '" title="' + escapeHtml(image.caption) + '" loading="lazy">').join('');
    const exportLink = $('export-link');
    if (result.can_publish && sessionId) {
      exportLink.href = '/api/v1/conversations/' + sessionId + '/export?format=markdown';
      exportLink.hidden = false;
    } else {
      exportLink.hidden = true;
    }
    const postersLink = $('posters-link');
    if (sessionId && draft.pages && draft.pages.length) {
      postersLink.href = '/api/v1/conversations/' + sessionId + '/posters.zip';
      postersLink.hidden = false;
    } else {
      postersLink.hidden = true;
    }
    const key = (draft.title || '') + '|' + (draft.body || '').slice(0, 20);
    if (key !== lastCoverKey) {
      lastCoverKey = key;
      $('cover-result').hidden = true;
      $('cover-image').removeAttribute('src');
    }
  }

  function renderPages(result) {
    const draft = result.draft;
    const grid = $('pages-grid');
    const empty = $('pages-empty');
    const tools = $('pages-tools');
    if (!draft || !draft.pages || !draft.pages.length) {
      grid.hidden = true;
      empty.hidden = false;
      if (tools) tools.hidden = true;
      return;
    }
    empty.hidden = true;
    grid.hidden = false;
    if (tools) {
      tools.hidden = false;
      const link = $('posters-link-pages');
      if (sessionId) link.href = '/api/v1/conversations/' + sessionId + '/posters.zip';
    }
    const handoff = result.handoff || {};
    const images = {};
    (handoff.images || []).forEach(image => { images[image.image_id] = image; });
    const facts = {};
    (handoff.facts || []).forEach(fact => { facts[fact.fact_id] = fact.text.split('\n')[0]; });
    grid.innerHTML = draft.pages.map((page, index) => {
      const image = images[page.image_id];
      const factChips = (page.fact_ids || []).map(id =>
        '<span class="fact-chip" title="' + escapeHtml(facts[id] || id) + '">' + escapeHtml(id) + '</span>').join('');
      return '<article class="page-card">' +
        '<div class="page-card-head"><span class="page-role">' + escapeHtml(ROLE_NAMES[page.role] || page.role) + '</span>' +
        '<span class="page-number">0' + (index + 1) + ' / 06</span></div>' +
        (image ? '<img class="page-image" src="' + escapeHtml(image.access_url) + '" alt="' + escapeHtml(image.caption) + '" loading="lazy">' : '') +
        '<h4 class="page-title">' + escapeHtml(page.title) + '</h4>' +
        '<p class="page-text">' + escapeHtml(page.text) + '</p>' +
        (factChips ? '<div class="page-facts">' + factChips + '</div>' : '') +
        '</article>';
    }).join('');
  }

  function renderEvidence(handoff) {
    const status = $('evidence-status');
    const list = $('evidence-list');
    const imagesWrap = $('evidence-images');
    const count = $('evidence-count');
    const tab = $('evidence-tab-content');
    if (!handoff) {
      status.innerHTML = '<p class="hint">完成选品后显示知识库状态、事实与图片。</p>';
      list.innerHTML = '';
      imagesWrap.innerHTML = '';
      tab.innerHTML = '<div class="empty-state"><div class="empty-icon">◈</div><h3>还没有证据</h3><p>完成选品后，这里会列出知识库事实、图片和来源。</p></div>';
      count.textContent = '0';
      return;
    }
    const state = handoff.data_state || {};
    const facts = handoff.facts || [];
    const images = handoff.images || [];
    const statusHtml = '<div class="evidence-kv">' +
      '<span>知识库</span><span>' + escapeHtml(state.knowledge_base || '—') + '</span>' +
      '<span>可生成</span><span>' + (state.can_generate ? '是' : '否') + '</span>' +
      '<span>赛事库</span><span>' + (state.competition_ready ? '已满足' : '个人库联调') + '</span>' +
      '<span>读取时间</span><span>' + escapeHtml((state.read_at || '').slice(0, 19).replace('T', ' ')) + '</span>' +
      '</div><p class="hint">' + escapeHtml((state.reasons || []).slice(0, 2).join('；')) + '</p>';
    status.innerHTML = statusHtml;
    count.textContent = String(facts.length);
    const listHtml = facts.map(fact => {
      const label = (fact.text || '').split('\n')[0];
      return '<div class="evidence-item"><div class="label">' + escapeHtml(label) + '</div>' +
        '<div class="source">来源：' + escapeHtml((fact.source && fact.source.title) || 'ima') + '</div></div>';
    }).join('');
    list.innerHTML = listHtml;
    const imagesHtml = images.map(image =>
      '<img src="' + escapeHtml(image.access_url) + '" alt="' + escapeHtml(image.caption) + '" title="' + escapeHtml(image.caption) + '" loading="lazy">').join('');
    imagesWrap.innerHTML = imagesHtml;
    tab.innerHTML = '<div class="evidence-status">' + statusHtml + '</div><div class="evidence-list evidence-tab-list">' + listHtml +
      '</div><div class="evidence-images">' + imagesHtml + '</div>';
  }

  function renderVerify(result) {
    const empty = $('verify-empty');
    const content = $('verify-content');
    const report = result.compliance_report;
    if (!report) {
      empty.hidden = false;
      content.hidden = true;
      content.innerHTML = '';
      return;
    }
    empty.hidden = true;
    content.hidden = false;
    const passed = report.passed;
    const approved = result.can_publish;
    const errors = report.errors || [];
    const warnings = report.warnings || [];
    let html = '<div class="verify-summary ' + (passed ? 'ok' : 'error') + '">' +
      '<strong>' + (approved ? '已通过确认，可以导出' : passed ? '规则扫描通过，等待你确认' : '规则扫描未通过') + '</strong>' +
      '<span>' + (passed ? '数字可追溯、未发现高风险用语；这不是逐条语义核验。' : '请先处理下面的问题，再确认发布。') + '</span></div>';
    html += errors.map(error => '<div class="verify-item error">问题：' + escapeHtml(error) + '</div>').join('');
    html += warnings.map(warning => '<div class="verify-item warning">提示：' + escapeHtml(warning) + '</div>').join('');
    html += '<div class="verify-item"><span class="hint">' + escapeHtml(report.note || '规则扫描不是完整语义核验，发布前仍需人工复核。') + '</span></div>';
    content.innerHTML = html;
  }

  function renderStepper(result) {
    if (!stepper) return;   // 五步指示器已删除；进度语义由板块与锁定状态承载
    let current = 1;
    if (result.slots && result.slots.audience && result.slots.budget && result.slots.scenario) current = 2;
    if (result.candidates && result.candidates.length) current = 2;
    if (result.selected) current = 3;
    if (result.draft) current = 3;
    if (result.compliance_report) current = 4;
    if (result.can_publish) current = 5;
    stepper.querySelectorAll('.step').forEach(step => {
      const index = Number(step.dataset.step);
      step.classList.toggle('active', index === current);
      step.classList.toggle('done', index < current);
    });
  }

  function updateQuickActions(result) {
    quickActions.hidden = !result.draft;
  }

  function updatePublishState(result) {
    const state = $('publish-state');
    if (result.can_publish) {
      state.textContent = '已通过确认，可以复制或导出';
      state.className = 'ready';
    } else if (result.compliance_report && !result.compliance_report.passed) {
      state.textContent = '核验未通过，请先修改';
      state.className = 'blocked';
    } else {
      state.textContent = '尚未通过核验，不可发布';
      state.className = '';
    }
  }

  function updateEngineBadge(draft) {
    const badge = $('engine-badge');
    if (!draft) {
      badge.textContent = selectedEngine === 'llm'
        ? '模型 · ' + (selectedModel || '默认')
        : '规则引擎';
      return;
    }
    const meta = draft.engine_meta || {};
    if (meta.provider === 'rule-fallback' || (meta.provider || '').startsWith('rule-fallback-compliance')) badge.textContent = '规则回退';
    else if (meta.provider === 'local-rule-template') badge.textContent = '规则引擎';
    else if (meta.model) badge.textContent = '模型 · ' + meta.model;
    else badge.textContent = '规则引擎';
  }

  function syncEngineControls() {
    const select = $('model-select');
    if (!select) return;
    if (selectedEngine === 'agent') select.value = 'agent';
    else if (selectedEngine === 'llm' && selectedModel) select.value = 'llm:' + selectedModel;
    else select.value = 'rule';
    if (!lastResult || !lastResult.draft) {
      $('engine-badge').textContent = selectedEngine === 'agent'
        ? '自主 Agent'
        : (selectedEngine === 'llm' ? '模型 · ' + (selectedModel || '默认') : '规则引擎');
    }
  }

  async function loadModels() {
    try {
      const response = await fetch('/api/v1/models');
      const data = await response.json();
      const select = $('model-select');
      const textModels = (data.models || (data.text || []).map(model => ({ id: model, label: model })));
      const defaultModel = data.default || (textModels[0] && textModels[0].id) || null;
      select.innerHTML = textModels.map(item =>
        '<option value="llm:' + escapeHtml(item.id) + '">' +
        (item.id === defaultModel ? '默认模型 · ' : '') + escapeHtml(item.label || item.id) +
        (item.context ? ' · ' + escapeHtml(item.context) : '') + '</option>').join('') +
        '<option value="custom" disabled>更多模型（待配置）</option>';
      if (defaultModel) {
        selectedEngine = 'llm';
        selectedModel = defaultModel;
      } else {
        selectedEngine = 'rule';
        selectedModel = null;
      }
      syncEngineControls();
    } catch (error) {
      selectedEngine = 'rule';
      selectedModel = null;
      syncEngineControls();
    }
  }

  function renderHotTopics(topics, note) {
    hotTopics = topics || [];
    currentHotTopics = hotTopics.slice(0, 3);
    const noteNode = $('hot-source-note');
    if (noteNode) noteNode.textContent = note || '';
    if (!$('hot-view').hidden) renderHotList();
    const wizard = optionsWrap.querySelector('.wizard');
    if (wizard && wizard.dataset.field === 'objective' && lastQuestions.length) {
      renderOptions(lastQuestions);
    }
  }

  async function loadHotspots(slots) {
    if (xhsTopicsActive || hotTopics.length) return;
    const scenario = (slots && slots.scenario) || '';
    const audience = (slots && slots.audience) || '';
    const key = scenario + '|' + audience;
    if (key === lastHotKey) return;
    lastHotKey = key;
    try {
      const response = await fetch('/api/v1/hotspots?scenario=' + encodeURIComponent(scenario) + '&audience=' + encodeURIComponent(audience));
      const data = await response.json();
      const note = data.data_mode === 'live' ? '小红书热榜'
        : data.data_mode === 'live_fallback' ? '微博热榜（跨平台参考）'
        : '编辑方向（热榜暂不可用）';
      renderHotTopics(data.topics || [], note);
    } catch (error) {
      renderHotTopics([], '热点暂不可用');
    }
  }

  function openXhsModal(message) {
    $('xhs-modal').hidden = false;
    if (message) $('xhs-status').textContent = message;
  }

  function closeXhsModal() {
    $('xhs-modal').hidden = true;
    if (xhsPollTimer) {
      clearInterval(xhsPollTimer);
      xhsPollTimer = null;
    }
  }

  async function loadXhsTopics() {
    const slots = (lastResult && lastResult.slots) || {};
    try {
      const response = await fetch('/api/v1/xhs/hot?scenario=' +
        encodeURIComponent(slots.scenario || '') + '&audience=' + encodeURIComponent(slots.audience || ''));
      const data = await response.json();
      if (response.status === 401) {
        xhsTopicsActive = false;
        openXhsModal(data.message || '请先扫码登录小红书');
        return xhsLogin();
      }
      if (!response.ok) {
        xhsTopicsActive = false;
        return loadPublicHotspots('小红书暂不可用，以下为公开热榜参考');
      }
      const topics = data.topics || [];
      if (!topics.length) {
        xhsTopicsActive = false;
        return loadPublicHotspots('小红书未返回相关笔记，以下为公开热榜参考');
      }
      xhsTopicsActive = true;
      const xhsBtn = $('xhs-login');
      if (xhsBtn) xhsBtn.textContent = '小红书已连接';
      renderHotTopics(topics, '小红书实时笔记');
    } catch (error) {
      xhsTopicsActive = false;
      return loadPublicHotspots('小红书连接失败，以下为公开热榜参考');
    }
  }

  async function loadPublicHotspots(note) {
    const slots = (lastResult && lastResult.slots) || {};
    try {
      const response = await fetch('/api/v1/hotspots?scenario=' +
        encodeURIComponent(slots.scenario || '') + '&audience=' + encodeURIComponent(slots.audience || ''));
      const data = await response.json();
      const sourceNote = data.data_mode === 'live' ? '小红书热榜'
        : data.data_mode === 'live_fallback' ? '微博热榜（跨平台参考）'
        : '编辑方向（公开热榜暂不可用）';
      renderHotTopics(data.topics || [], (note ? note + ' · ' : '') + sourceNote);
    } catch (error) {
      renderHotTopics([], '热点暂不可用');
    }
  }

  function renderHotList() {
    const list = $('hot-list');
    if (!hotTopics.length) {
      list.innerHTML = '<div class="empty-state"><div class="empty-icon">🔥</div><h3>暂无热点</h3>' +
        '<p>可以点“连接小红书”扫码授权，或稍后刷新公开热榜。</p></div>';
      return;
    }
    list.innerHTML = hotTopics.map((topic, index) => {
      const heat = topic.heat ? '热度 ' + escapeHtml(String(topic.heat)) : '';
      const source = topic.source === 'xiaohongshu-mcp' ? '小红书' : (topic.source || '公开热榜');
      return '<button type="button" class="hot-item' + (index === selectedHotIndex ? ' active' : '') +
        '" data-hot-index="' + index + '">' +
        '<span class="hot-rank">' + String(index + 1).padStart(2, '0') + '</span>' +
        '<span class="hot-main"><strong>' + escapeHtml(topic.title) + '</strong>' +
        '<span class="hint">' + escapeHtml(source) + (heat ? ' · ' + heat : '') + '</span></span>' +
        '<span class="hot-arrow">›</span></button>';
    }).join('');
  }

  function showHotDetail(index) {
    const topic = hotTopics[index];
    if (!topic) return;
    selectedHotIndex = index;
    renderHotList();
    const detail = $('hot-detail');
    detail.innerHTML = '<article class="hot-card">' +
      '<span class="eyebrow">热点 ' + String(index + 1).padStart(2, '0') + '</span>' +
      '<h3>' + escapeHtml(topic.title) + '</h3>' +
      '<p class="hint">来源：' + escapeHtml(topic.source || '公开热榜') +
      (topic.heat ? ' · 热度 ' + escapeHtml(String(topic.heat)) : '') + '</p>' +
      '<div class="hot-detail-actions">' +
      '<button type="button" class="btn-secondary" id="hot-detail-toggle">查看热点详情</button>' +
      '<button type="button" class="btn-primary" id="hot-use">使用热点</button></div>' +
      '<div class="hot-detail-body" id="hot-detail-body" hidden>' +
      '<div class="evidence-kv"><span>标题</span><span>' + escapeHtml(topic.title) + '</span>' +
      '<span>来源</span><span>' + escapeHtml(topic.source || '公开热榜') + '</span>' +
      '<span>热度</span><span>' + escapeHtml(String(topic.heat || '未提供')) + '</span>' +
      '<span>关系</span><span>' + escapeHtml(topic.relation || '参考') + '</span>' +
      '<span>链接</span><span>' + (topic.url
        ? '<a href="' + escapeHtml(topic.url) + '" target="_blank" rel="noreferrer">打开原帖 ↗</a>'
        : '未提供') + '</span></div>' +
      '<p class="hint">热点只是切入线索，不是产品事实；生成时仍以官方知识库为准。</p></div></article>';
  }

  function useHotTopic(index) {
    const topic = hotTopics[index];
    if (!topic) return;
    const lines = ['参考热点：' + topic.title];
    if (topic.url) lines.push('热点链接：' + topic.url);
    lines.push('我的想法：');
    input.value = lines.join('\n');
    sendBtn.disabled = false;
    switchView('chat');
    input.focus();
    const end = input.value.length;
    input.setSelectionRange(end, end);
    toast('热点已带入对话框，补充你的想法后发送');
  }

  // 当前选中的交付面。朋友圈有两个面（个人/门店），所以这里显式区分，不猜。
  function currentSurface() {
    const active = document.querySelector('.surface-btn.active');
    const key = active ? active.dataset.surface : 'xiaohongshu';
    if (key !== 'moments') return key;
    const split = $('moments-split');
    const chosen = split ? split.querySelector('button.active') : null;
    return chosen ? chosen.dataset.moments : 'store_moments';
  }

  function surfaceLabel(surface) {
    return SURFACE_LABELS[surface] || surface;
  }

  // 点选项卡变蓝就是锁定：没有单独的锁定按钮。选项一变蓝，就把本次会话固定到该交付面，
  // 后端存进 task state 并作为指令进入系统提示词，所以它会真的改变写出来的东西。
  // 请求失败时回读服务端状态并回退，避免界面显示已锁定而实际没有。
  async function lockSurface(surface) {
    if (!sessionId) { applyLockState(null); return; }
    try {
      const response = await fetch('/api/v2/conversations/' + sessionId + '/surface', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ surface: surface }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        const names = (data.ambiguous || []).join(' / ');
        toast(data.message || ('锁定失败' + (names ? '：' + names : '')));
        await loadSurfaceLock();
        return;
      }
      applyLockState(data);
    } catch (error) {
      toast('锁定请求失败');
      await loadSurfaceLock();
    }
  }

  function applyLockState(info) {
    const state = $('lock-state');
    if (!state) return;
    const locked = Boolean(info && info.surface);
    state.hidden = !locked;
    state.textContent = locked ? ('已锁定 · ' + surfaceLabel(info.surface)) : '';
  }

  async function loadSurfaceLock() {
    if (!sessionId) { applyLockState(null); return; }
    try {
      const info = await fetch('/api/v2/conversations/' + sessionId + '/surface')
        .then((response) => response.json());
      applyLockState(info);
      if (info && info.surface) {
        const key = (info.surface === 'personal_moments' || info.surface === 'store_moments')
          ? 'moments' : info.surface;
        document.querySelectorAll('.surface-btn').forEach(
          (item) => item.classList.toggle('active', item.dataset.surface === key));
        const split = $('moments-split');
        if (split && key === 'moments') {
          split.hidden = false;
          split.querySelectorAll('button').forEach(
            (item) => item.classList.toggle('active', item.dataset.moments === info.surface));
        }
        selectedPlatform = info.surface;
      }
    } catch (error) {
      applyLockState(null);
    }
  }

  function switchView(view) {
    const hot = view === 'hot';
    const workspace = view === 'workspace';
    const studio = view === 'studio';
    $('chat-view').hidden = hot || workspace || studio;
    $('hot-view').hidden = !hot;
    $('studio-view').hidden = !studio;
    $('workspace-view').hidden = !workspace;
    // 板块按钮跟随视图，点侧栏或热点详情返回时也一致
    document.querySelectorAll('.section-btn').forEach((item) => item.classList.toggle(
      'active', item.dataset.section === (hot ? 'hot' : (studio ? 'studio'
        : (workspace ? '' : 'chat')))));
    renderSectionTabs(hot ? 'hot' : (studio ? 'studio' : 'chat'));
    if (hot) loadOrganizedTopics(false);
    if (workspace && window.WorkspaceApp) window.WorkspaceApp.open();
  }

  // 每个板块自己的小选项卡。对话的选项卡是标题栏中间的交付面，所以这里只有热点榜单有。
  function renderSectionTabs(section) {
    const host = $('section-tabs');
    if (!host) return;
    const tabs = SECTION_TABS[section] || [];
    host.hidden = tabs.length === 0;
    host.innerHTML = tabs.map(([key, label]) =>
      '<button type="button" class="section-tab" data-view="' + key + '">' + label +
      '</button>').join('');
    if (!tabs.length) return;
    const first = host.querySelector('.section-tab');
    if (first) first.classList.add('active');
  }

  // 热点榜单：点刷新 = 取公开热榜 + 让模型整理成热点清单（不排序、不打分）
  async function loadOrganizedTopics(force) {
    const note = $('hot-source-note');
    if (note) note.textContent = force ? '正在取源并让模型整理…（约 10–60 秒）' : '正在加载热点清单…';
    const list = $('hot-list');
    if (list) list.innerHTML = '<p class="hint">加载中…</p>';
    try {
      const response = force
        ? await fetch('/api/v1/hot-topics/refresh', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ limit: 50 }),
          })
        : await fetch('/api/v1/hot-topics?limit=50');
      const data = await response.json();
      renderOrganizedTopics(data);
    } catch (error) {
      if (note) note.textContent = '加载失败：' + error;
      if (list) list.innerHTML = '<p class="hint">加载失败，请点刷新重试。</p>';
    }
  }

  // 把热点复用进对话：带上名称、短文与搜索词，让模型基于热点本身写文案。
  // 不抓链接内容——服务端抓小红书需要登录态，会撞风控（见 docs/15）。
  let organizedCache = [];
  function useTopic(index) {
    const topic = organizedCache[index];
    if (!topic) return;
    switchView('chat');
    const parts = ['用这个热点写一条华为 PC 的文案。',
                   '热点：' + topic.topic,
                   '类别：' + topic.kind,
                   '说明：' + (topic.intro || topic.gist)];
    if ((topic.keywords || []).length) parts.push('相关搜索词：' + topic.keywords.join('、'));
    parts.push('注意：热点只作选题背景，产品事实仍以知识库为准，不得编造热点细节。');
    const text = parts.join('\n');
    input.value = text;
    input.focus();
    toast('热点已带入输入框，可先改再发送');
  }

  function renderOrganizedTopics(data) {
    const note = $('hot-source-note');
    const list = $('hot-list');
    const detail = $('hot-detail');
    if (!data || !data.ok) {
      if (note) note.textContent = (data && (data.message || data.error)) || '没有可用的热点清单';
      if (list) list.innerHTML = '<p class="hint">' +
        escapeHtml((data && (data.message || data.error)) || '暂无可展示内容') + '</p>';
      return;
    }
    const src = data.source || {};
    if (note) {
      note.textContent = '来源：' + (src.source || '') + ' · 取到 ' + src.available + ' 条'
        + (src.cap_note ? '（' + src.cap_note + '）' : '')
        + ' · 品牌过滤剔除 ' + (data.excluded_by_brand || []).length + ' 条'
        + ' · 整理出 ' + data.topic_count + ' 条热点'
        + (data.cached ? ' · 缓存' : ' · 刚刚生成');
    }
    const topics = data.topics || [];
    organizedCache = topics;
    list.innerHTML = topics.length
      ? topics.map((topic, index) =>
          '<article class="topic-card" data-topic="' + index + '">' +
          '<div class="topic-head"><span class="topic-kind">' + escapeHtml(topic.kind) + '</span>' +
          '<strong>' + escapeHtml(topic.topic) + '</strong></div>' +
          '<p class="topic-gist">' + escapeHtml(topic.gist) + '</p>' +
          (topic.intro ? '<p class="topic-intro">' + escapeHtml(topic.intro) + '</p>' : '') +
          '<div class="topic-actions">' +
          '<button type="button" class="chip" data-use-topic="' + index + '">用这个热点写文案</button>' +
          ((topic.links || [])[0]
            ? '<a class="chip" href="' + escapeHtml(topic.links[0]) + '" target="_blank" rel="noopener">在小红书打开 ↗</a>'
            : '') +
          ((topic.keywords || []).length
            ? '<span class="topic-kw">搜索词：' + topic.keywords.map(escapeHtml).join(' ／ ') + '</span>'
            : '') +
          '</div>' +
          '<p class="topic-src">原始条目：' + (topic.source_titles || [])
            .map(escapeHtml).join(' ／ ') + '</p>' +
          '</article>').join('')
      : '<p class="hint">这一轮没有整理出好玩的热点（剔除了新闻事件与其他品牌后为空，这是正常结果）。</p>';
    if (detail) {
      detail.innerHTML = '<div class="empty-state"><div class="empty-icon">✦</div>' +
        '<h3>清单说明</h3><p>' + escapeHtml(data.notice || '') + '</p>' +
        '<p class="hint">' + escapeHtml(src.time_note || '') + '</p>' +
        '<p class="hint">规则：偏好玩 · 跳过新闻事件 · 不涉及其他数码品牌</p></div>';
    }
  }

  async function loadHotList() {
    const note = $('hot-source-note');
    note.textContent = '正在加载小红书热点…';
    try {
      const status = await fetch('/api/v1/xhs/status').then(r => r.json()).catch(() => ({}));
      if (!status.configured) {
        return loadPublicHotspots('小红书未配置，以下为公开热榜参考');
      }
      if (!status.logged_in) {
        return loadPublicHotspots('未登录小红书，以下为公开热榜参考；扫码后显示实时笔记');
      }
      return loadXhsTopics();
    } catch (error) {
      return loadPublicHotspots('热点加载失败，以下为公开热榜参考');
    }
  }

  function startXhsPoll() {
    if (xhsPollTimer) clearInterval(xhsPollTimer);
    const started = Date.now();
    xhsPollTimer = setInterval(async () => {
      try {
        const response = await fetch('/api/v1/xhs/login/status');
        const data = await response.json();
        if (data.logged_in) {
          closeXhsModal();
          toast('小红书已连接');
          loadXhsTopics();
          return;
        }
        const waited = Date.now() - started;
        if (waited > 15000 && waited < 200000) {
          $('xhs-status').textContent = waited > 40000
            ? '仍未登录：手机已确认也没反应时，通常是云端风控；可改用服务端 Cookie 导入（见连接上线指南）或先用公开热榜'
            : '等待扫码；如果已经扫码，请在手机上点“确认登录”';
        }
        if (waited > 200000) {
          $('xhs-status').textContent = '二维码已过期，请点“刷新二维码”重新获取';
          clearInterval(xhsPollTimer);
          xhsPollTimer = null;
        }
      } catch (error) {
        // Keep polling; a transient network error should not close the dialog.
      }
    }, 2000);
  }

  async function xhsLogin() {
    if (xhsPollTimer) {
      clearInterval(xhsPollTimer);
      xhsPollTimer = null;
    }
    try {
      const response = await fetch('/api/v1/xhs/login/qrcode');
      const data = await response.json();
      if (!response.ok) {
        toast(data.message || '小红书未配置');
        openXhsModal(data.message || '小红书未配置');
        return;
      }
      if (data.is_logged_in) {
        closeXhsModal();
        return loadXhsTopics();
      }
      if (!data.qr_image) {
        openXhsModal('未获取到二维码，请稍后重试');
        return;
      }
      $('xhs-qr').src = data.qr_image;
      $('xhs-status').textContent = '请用小红书 App 扫码登录，并在手机上点“确认登录”';
      openXhsModal();
      startXhsPoll();
    } catch (error) {
      toast('小红书连接失败');
      openXhsModal('小红书连接失败，请检查 MCP 服务');
    }
  }

  async function loadXhsStatus() {
    const button = $('xhs-login');
    if (!button) return;   // 顶栏的小红书按钮已按用户要求删除
    try {
      const response = await fetch('/api/v1/xhs/status');
      const data = await response.json();
      if (!response.ok || !data.configured) {
        button.textContent = '小红书未配置';
        button.title = '部署时设置 XHS_MCP_BASE 与 XHS_MCP_TOKEN';
        return;
      }
      if (data.logged_in) {
        button.textContent = '小红书已连接';
        loadXhsTopics();
      } else {
        button.textContent = '连接小红书';
      }
    } catch (error) {
      button.textContent = '连接小红书';
    }
  }

  async function loadKnowledgeBase() {
    const badge = $('kb-badge');
    const pillText = $('kb-pill-text');
    const pillDot = $('kb-dot');
    const ctxText = $('kb-ctx-text');
    const paint = (label, state, ctxLabel, title) => {
      if (pillText) pillText.textContent = label;
      if (pillDot) pillDot.className = 'status-dot' + (state ? ' ' + state : '');
      if (ctxText) ctxText.textContent = ctxLabel;
      if (pillDot && title) pillDot.parentElement.title = title;
    };
    try {
      const response = await fetch('/api/v1/knowledge/connection');
      const data = await response.json();
      if (response.ok && data.status === 'ok') {
        const name = data.knowledge_base_name || '';
        const local = name.includes('本地官方');
        badge.textContent = local ? '知识库：本地官方库 v7' : '知识库：已连接 · ' + name;
        badge.title = name;
        badge.className = 'badge ok';
        // 只有后端明确返回 status=ok 才显示已连接；其余一律如实显示未连接。
        paint(local ? '本地官方库 v7' : '已连接', 'ok',
              '已连接 · 只读检索产品事实', '知识库：' + name);
      } else {
        // 面向用户只说"未连接"这一类结论；后端 reason 可能含服务端配置提示，
        // 那是给管理员的，不放进界面 tooltip。
        badge.textContent = '知识库：暂不可用';
        badge.title = '知识库未连接：当前无法核验产品事实';
        badge.className = 'badge err';
        paint('知识库：未连接', 'err', '未连接 · 无法核验产品事实',
              '知识库未连接：当前无法核验产品事实');
      }
    } catch (error) {
      badge.textContent = '知识库：检测失败';
      badge.className = 'badge err';
      paint('知识库：检测失败', 'err', '检测失败 · 无法核验产品事实', '知识库状态检测失败');
    }
  }

  async function generateCover() {
    const draft = lastResult && lastResult.draft;
    if (!draft) return;
    const slots = lastResult.slots || {};
    const button = $('generate-cover');
    button.disabled = true;
    button.textContent = '生成中…';
    try {
      const prompt = '小红书封面氛围背景，主题「' + (draft.title || '') + '」，场景' + (slots.scenario || '') +
        '，受众' + (slots.audience || '') + '，华为品牌蓝与浅灰，留白，光影柔和，无文字，无产品主体。';
      const response = await fetch('/api/v1/covers/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || '生成失败');
      $('cover-image').src = data.access_url;
      $('cover-result').hidden = false;
      toast('封面氛围图已生成');
    } catch (error) {
      toast(error.message || '封面生成失败');
    } finally {
      button.disabled = false;
      button.textContent = '生成 AI 封面氛围图';
    }
  }

  function switchTab(name) {
    const workTabs = $('work-tabs');
    if (!workTabs) return;   // 工作栏已删除
    workTabs.querySelectorAll('.tab').forEach(item => item.classList.toggle('active', item.dataset.tab === name));
    document.querySelectorAll('.tab-panel').forEach(panel => { panel.hidden = true; });
    const panel = $('panel-' + name);
    if (panel) panel.hidden = false;
  }

  function renderWorkTabs(platform) {
    if (!$('work-tabs')) return;   // 工作栏已删除
    const list = PLATFORM_TABS[platform] || PLATFORM_TABS.xiaohongshu;
    $('work-tabs').innerHTML = list.map(([key, label], index) =>
      '<button class="tab' + (index === 0 ? ' active' : '') + '" data-tab="' + key + '" role="tab">' +
      label + '</button>').join('');
    switchTab(list[0][0]);
  }

  function populateSamplePages() {
    const wrap = $('sample-pages');
    if (!wrap) return;
    wrap.innerHTML = Array.from({ length: 6 }, (_, index) => {
      const number = String(index + 1).padStart(2, '0');
      return '<a class="sample-page" href="/static/samples/sketch/page-' + number + '.png" target="_blank" rel="noreferrer">' +
        '<img src="/static/samples/sketch/page-' + number + '.png" alt="样稿第' + number + '页" loading="lazy"><span>第 ' + number + ' 页</span></a>';
    }).join('');
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    addMessage('operator', '<p>' + escapeHtml(text) + '</p>');
    input.value = '';
    sendBtn.disabled = true;
    send(text);
  });

  input.addEventListener('input', () => {
    sendBtn.disabled = !input.value.trim();
  });

  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  quickActions.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    addMessage('operator', '<p>' + escapeHtml(button.dataset.action) + '</p>');
    send(button.dataset.action);
  });

  optionsWrap.addEventListener('click', (event) => {
    if (turnInFlight || Date.now() - optionsRenderedAt < 350) return;
    const hotspot = event.target.closest('button[data-wizard-hotspot]');
    if (hotspot) {
      wizardHotspot = wizardHotspot === hotspot.dataset.wizardHotspot ? null : hotspot.dataset.wizardHotspot;
      optionsWrap.querySelectorAll('button[data-wizard-hotspot]').forEach(button => {
        button.classList.toggle('active', button.dataset.wizardHotspot === wizardHotspot);
      });
      return;
    }
    const button = event.target.closest('button[data-message]');
    if (!button) return;
    addMessage('operator', '<p>' + escapeHtml(button.textContent) + '</p>');
    const slot = button.dataset.slot ? JSON.parse(button.dataset.slot) : null;
    const wizard = optionsWrap.querySelector('.wizard');
    const field = wizard && wizard.dataset.field;
    optionsWrap.hidden = true;
    optionsWrap.innerHTML = '';
    if (field === 'objective' && wizardHotspot) {
      send(button.dataset.message, { hotspot: wizardHotspot });
      wizardHotspot = null;
      return;
    }
    send(slot ? '' : button.dataset.message, slot);
  });

  $('candidate-board').addEventListener('click', (event) => {
    const pick = event.target.closest('button[data-pick]');
    if (pick) {
      const text = pick.getAttribute('data-pick');
      addMessage('operator', '<p>' + escapeHtml(text) + '</p>');
      send(text);
      return;
    }
    const button = event.target.closest('button[data-select]');
    if (!button) return;
    const text = '选' + button.dataset.select;
    addMessage('operator', '<p>' + escapeHtml(text) + '</p>');
    send(text);
  });

  // 示例按钮从空状态挪到了输入框上方；开始对话后收起，不再占位置。
  on('starter-row', 'click', (event) => {
    const button = event.target.closest('button[data-example]');
    if (!button) return;
    input.value = button.dataset.example;
    form.requestSubmit();
  });

  // 放大输入框：长文案编辑时用
  on('composer-expand', 'click', () => {
    const box = document.querySelector('.composer');
    const expanded = box.classList.toggle('expanded');
    const button = $('composer-expand');
    if (button) {
      button.title = expanded ? '还原输入框' : '放大输入框';
      button.textContent = expanded ? '⤡' : '⤢';
    }
    input.focus();
  });

  $('hot-list').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-hot-index]');
    if (!button) return;
    showHotDetail(Number(button.dataset.hotIndex));
  });

  $('hot-detail').addEventListener('click', (event) => {
    if (event.target.closest('#hot-detail-toggle')) {
      const body = $('hot-detail-body');
      const toggle = $('hot-detail-toggle');
      if (body) {
        body.hidden = !body.hidden;
        toggle.textContent = body.hidden ? '查看热点详情' : '收起热点详情';
      }
      return;
    }
    if (event.target.closest('#hot-use')) useHotTopic(selectedHotIndex);
  });

  on('hot-back', 'click', () => switchView('chat'));
  on('studio-back', 'click', () => switchView('chat'));
  $('hot-refresh').addEventListener('click', () => loadOrganizedTopics(true));
  $('hot-connect').addEventListener('click', xhsLogin);

  on('xhs-refresh', 'click', xhsLogin);
  $('xhs-fallback').addEventListener('click', () => {
    closeXhsModal();
    switchView('hot');
    toast('已切换到公开热榜参考');
  });
  $('xhs-close').addEventListener('click', closeXhsModal);
  $('xhs-modal').addEventListener('click', (event) => {
    if (event.target.id === 'xhs-modal') closeXhsModal();
  });

  const surfaceTabs = $('surface-tabs');
  if (surfaceTabs) surfaceTabs.addEventListener('click', (event) => {
    const button = event.target.closest('.surface-btn');
    if (!button) return;
    surfaceTabs.querySelectorAll('.surface-btn').forEach(
      (item) => item.classList.toggle('active', item === button));
    const split = $('moments-split');
    if (split) split.hidden = button.dataset.surface !== 'moments';
    selectedPlatform = currentSurface();
    // 变蓝即锁定；朋友圈要先在“门店/个人”里选一个，所以那时才提交。
    if (button.dataset.surface === 'moments') {
      const chosen = split ? split.querySelector('button.active') : null;
      if (chosen) lockSurface(chosen.dataset.moments);
    } else {
      lockSurface(currentSurface());
    }
  });
  const momentsSplit = $('moments-split');
  if (momentsSplit) momentsSplit.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-moments]');
    if (!button) return;
    momentsSplit.querySelectorAll('button').forEach(
      (item) => item.classList.toggle('active', item === button));
    selectedPlatform = currentSurface();
    lockSurface(currentSurface());
  });
  $('context-toggle').addEventListener('click', () => {
    $('ctx-menu').hidden = !$('ctx-menu').hidden;
  });

  $('ctx-menu').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-ctx]');
    if (!button) return;
    $('ctx-menu').hidden = true;
    if (button.dataset.ctx === 'hot') {
      switchView('hot');
      return;
    }
    toast('官方知识库已连接，生成时会自动检索产品事实');
  });

  $('model-select').addEventListener('change', (event) => {
    const value = event.target.value;
    if (value === 'rule') {
      selectedEngine = 'rule';
      selectedModel = null;
    } else if (value === 'agent') {
      selectedEngine = 'agent';
      selectedModel = null;
    } else {
      selectedEngine = 'llm';
      selectedModel = value.slice(4);
    }
    syncEngineControls();
  });

  on('work-tabs', 'click', (event) => {
    const tab = event.target.closest('button[data-tab]');
    if (tab) switchTab(tab.dataset.tab);
  });

  $('copy-draft').addEventListener('click', async () => {
    const draft = lastResult && lastResult.draft;
    if (!draft) return;
    const text = draft.title + '\n\n' + draft.body + '\n\n' + (draft.tags || []).map(tag => '#' + tag).join(' ');
    try {
      await navigator.clipboard.writeText(text);
      toast('已复制标题和正文');
    } catch (error) {
      toast('复制失败，请手动选中文字');
    }
  });

  async function loadImageStyles() {
    try {
      const response = await fetch('/api/v1/images/styles');
      const data = await response.json();
      const select = $('image-style');
      select.innerHTML = (data.styles || []).map(style =>
        '<option value="' + escapeHtml(style.id) + '">' + escapeHtml(style.label) + '</option>').join('');
      if (!select.options.length) select.innerHTML = '<option value="clean_desk">清爽桌搭</option>';
    } catch (error) {
      $('image-style').innerHTML = '<option value="clean_desk">清爽桌搭</option>';
    }
  }

  async function generateImages() {
    const button = $('generate-images');
    if (!sessionId) { toast('请先完成需求澄清与选品，拿到产品原图后再生图'); return; }
    const original = button.textContent;
    button.disabled = true;
    button.textContent = '生成中…';
    try {
      const source = $('image-source').value;
      const response = await fetch('/api/v1/images/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, source: source, style: $('image-style').value,
          ratio: $('image-ratio').value, n: 1,
          prompt: source === 'ai' ? (($('image-prompt').value || '').trim() || null) : null }),
      });
      const data = await response.json();
      if (!response.ok) { toast(data.message || '生图失败'); return; }
      const wrap = $('image-results');
      wrap.hidden = false;
      const caption = data.source === 'kb' ? '知识库原图' : 'AI 生成 · 非产品实拍';
      wrap.innerHTML = data.images.map(url =>
        '<figure><img src="' + escapeHtml(url) + '" alt="' + escapeHtml(caption) + '" loading="lazy">' +
        '<figcaption>' + escapeHtml(caption) + '</figcaption></figure>').join('') +
        '<p class="hint">' + escapeHtml(data.note) + (data.model ? ' · 模型 ' + escapeHtml(data.model) : '') + '</p>';
      toast(data.source === 'kb' ? '已使用知识库原图' : '已生成图片');
    } catch (error) {
      toast('生图失败，请稍后重试');
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  }

  $('generate-images').addEventListener('click', generateImages);
  $('image-source').addEventListener('change', () => {
    const ai = $('image-source').value === 'ai';
    $('image-style').disabled = !ai;
    $('image-prompt').disabled = !ai;
    $('generate-images').textContent = ai ? '一键生图' : '使用知识库原图';
  });
  $('generate-cover').addEventListener('click', generateCover);

  $('new-task').addEventListener('click', newTask);
  $('task-list').addEventListener('click', (event) => {
    if (batchMode) {
      if (event.target.closest('input[data-check]')) {
        const box = event.target.closest('input[data-check]');
        if (box.checked) batchSelected.add(box.dataset.check);
        else batchSelected.delete(box.dataset.check);
        syncBatchBar();
      }
      return;   // 批量模式下点任务只勾选，不打开
    }
    const more = event.target.closest('[data-menu]');
    if (more) {
      event.stopPropagation();
      showTaskMenu(more.getAttribute('data-menu'), more);
      return;
    }
    const open = event.target.closest('[data-open]');
    if (open) {
      hideTaskMenu();
      openTask(open.getAttribute('data-open'));
    }
  });
  $('task-list').addEventListener('scroll', hideTaskMenu);
  on('task-batch-toggle', 'click', () => setBatchMode(!batchMode));
  on('task-batch-cancel', 'click', () => setBatchMode(false));
  on('task-batch-delete', 'click', deleteSelectedTasks);
  on('task-select-all', 'change', (event) => {
    batchSelected.clear();
    if (event.target.checked) taskCache.forEach((task) => batchSelected.add(task.conversation_id));
    renderTasks();
  });
  $('task-menu').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-task-action]');
    if (!button || !taskMenuId) return;
    const action = button.getAttribute('data-task-action');
    const id = taskMenuId;
    const task = taskCache.find(item => item.conversation_id === id);
    hideTaskMenu();
    if (action === 'pin') {
      await patchTask(id, { pinned: !(task && task.pinned) });
    } else if (action === 'rename') {
      const name = prompt('任务名称', (task && task.title) || '新任务');
      if (name && name.trim()) await patchTask(id, { title: name.trim() });
    } else if (action === 'delete') {
      await deleteTask(id);
    }
  });
  document.addEventListener('click', (event) => {
    const use = event.target.closest('[data-use-topic]');
    if (use) { useTopic(Number(use.getAttribute('data-use-topic'))); return; }
    if (event.target.closest('#task-menu') || event.target.closest('[data-menu]')) return;
    hideTaskMenu();
  });
  // 板块切换：对话 / 热点榜单 / 图文工作室
  const sectionNav = $('section-nav');
  if (sectionNav) {
    sectionNav.addEventListener('click', (event) => {
      const button = event.target.closest('.section-btn');
      if (button) switchView(button.dataset.section);
    });
  }
  const sectionTabs = $('section-tabs');
  if (sectionTabs) {
    sectionTabs.addEventListener('click', (event) => {
      const tab = event.target.closest('.section-tab');
      if (!tab) return;
      sectionTabs.querySelectorAll('.section-tab').forEach(
        (item) => item.classList.toggle('active', item === tab));
      hotScenarioView = tab.dataset.view;
      loadHotList();
    });
  }
  $('sidebar-toggle').addEventListener('click', () => {
    setSidebar(!sidebarIsOpen());
  });
  $('sidebar-collapse').addEventListener('click', () => setSidebar(false));

  document.addEventListener('keydown', (event) => {
    const meta = event.metaKey || event.ctrlKey;
    if (!meta) return;
    if (event.key.toLowerCase() === 'n') {
      event.preventDefault();
      newTask();
    } else if (event.key.toLowerCase() === 'k') {
      event.preventDefault();
      switchView('hot');
    }
  });

  let workPanelOpen = true;
  try {
    workPanelOpen = localStorage.getItem(WORK_PANEL_KEY) !== '0';
  } catch (error) {
    workPanelOpen = true;
  }
  setWorkPanel(workPanelOpen);
  // Restore the sidebar preference only for wide screens; narrow screens always start
  // closed so the overlay does not cover the conversation.
  let sidebarOpenPref = true;
  try {
    sidebarOpenPref = localStorage.getItem(SIDEBAR_KEY) !== '0';
  } catch (error) {
    sidebarOpenPref = true;
  }
  setSidebar(isNarrow() ? false : sidebarOpenPref);
  window.addEventListener('resize', () => {
    if (isNarrow()) {
      $('sidebar').classList.remove('collapsed');
      document.body.classList.remove('sidebar-open');
    } else {
      document.body.classList.remove('sidebar-open');
      setSidebar(sidebarOpenPref);
    }
  });
  renderWorkTabs(selectedPlatform);
  loadTasks();

  window.AgentApp = { getSessionId: () => sessionId, getHotTopics: () => hotTopics, switchView };

  loadKnowledgeBase();
  loadImageStyles();
  loadXhsStatus();
  loadModels();
  populateSamplePages();
  input.focus();
})();
