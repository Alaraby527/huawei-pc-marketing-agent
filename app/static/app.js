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
    // 热点榜单的子选项卡是头部那行「热梗 / 抖音热榜 / 微博热搜 / 知乎热榜 / 小红书热榜」，
    // 由 renderHotModeTabs 渲染，所以这里留空。
    // 原来这里是旧热点方案的 5 个「场景视图」（小红书/公众号/朋友圈·个人/朋友圈·门店/门店海报），
    // 那些视图从未接上数据，留着只会和交付面选项卡混淆。
    hot: [],
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
  let wizardHotspot = null;
  let optionsRenderedAt = 0;
  let turnInFlight = false;
  const SEND_ICON = sendBtn.innerHTML;

  const PLATFORM_NAMES = { xiaohongshu: '小红书', wechat_article: '公众号', moments: '门店朋友圈', poster: '门店海报' };
  const FIELD_NAMES = { audience: '客户画像', budget: '预算', scenario: '使用场景', preferences: '偏好', required_software: '必用软件 / 系统', objective: '内容目标' };
  const TASKS_KEY = 'huawei-agent-tasks-v1';
  const WORK_PANEL_KEY = 'huawei-agent-work-panel';
  const SIDEBAR_KEY = 'huawei-agent-sidebar';
  // Keep in sync with the @media (max-width:980px) block in style.css.
  const NARROW_QUERY = '(max-width:980px)';
  const WELCOME_HTML = chatLog.innerHTML;
  let lastUserText = '';
  // D：最近一次交付（文案 + 图片身份），只由渲染函数写入，供「去图文工作室」带入。
  let lastDelivery = {copy: '', imageIds: [], imageUrls: []};

  // F：三篇文案的选项卡状态。
  // 单击＝选中，双击＝放大；只有"选中的那一篇"会被「图文工作室」与「一键导出」消费。
  let selectedVariant = null;      // {variant, images, material, board}
  let variantClickTimer = null;    // 单击延迟计时器（双击冲突处理）
  let variantPending = null;       // 等待执行的单击 {board, tab}
  let variantZoomEl = null;        // 放大浮层（全局单例，见 ensureVariantZoom）
  const VARIANT_CLICK_DELAY = 250; // 规格：单击后延迟约 250ms 再执行选中

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text == null ? '' : String(text);
    return div.innerHTML;
  }

  // 放进 HTML 属性（如 data-ref-title="…"）时必须连引号一起转义：
  // escapeHtml 走的是文本节点，不会转义引号，热点标题里带 ASCII 引号就会把属性截断。
  function escapeAttr(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/"/g, '&quot;')
      .replace(/</g, '&lt;').replace(/>/g, '&gt;');
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

  // ===== A/B：候选卡与交付卡进入对话流 =====
  // #candidate-board / #draft-card 始终是同一个 DOM 节点：appendChild 只是把节点搬到
  // assistant 消息内部（不是克隆），所以 id、内容与已绑定的事件监听（$('candidate-board')
  // 的点击、$('copy-draft') / $('generate-images') 等）全部保留，既有逻辑不用改写。
  function lastAssistantMessage() {
    const rows = chatLog.querySelectorAll('.msg.assistant');
    return rows.length ? rows[rows.length - 1] : null;
  }

  // 有明确的 messageEl 就挂它；否则挂到最后一条 assistant 消息（切换任务回放时没有 messageEl）。
  function deliveryHost(messageEl) {
    return messageEl || lastAssistantMessage() || addMessage('assistant', '');
  }

  function attachToMessage(node, messageEl) {
    if (!node) return null;
    const host = deliveryHost(messageEl);
    if (node.parentNode !== host) host.appendChild(node);
    return node;
  }

  // newTask / openTask / 批量删除会整体重置 chat-log；先搬回停机位，避免被 innerHTML 销毁。
  function parkDelivery() {
    const park = $('delivery-park');
    const board = $('candidate-board');
    const card = $('draft-card');
    const entry = $('delivery-entry');
    if (park && board) { board.hidden = true; park.appendChild(board); }
    if (park && card) { card.hidden = true; park.appendChild(card); }
    if (park && entry) { entry.hidden = true; park.appendChild(entry); }
    lastDelivery = {copy: '', imageIds: [], imageUrls: []};
    syncDeliveryEntry();
  }

  // 修 Bug#1「交付卡带着上一轮内容跟到新消息」。
  // 机制：#draft-card / #candidate-board / #image-results 是**共享节点被 appendChild 搬运**，
  // 而重渲染只在"这一轮真的调用了对应渲染函数"时才发生。于是某一轮没有配图/没有草稿时，
  // 节点会带着上一轮的内容被搬进新消息，旧型号、旧原图看起来就像这一轮的产出。
  // 修法：把"清空共享节点"和"重新填充"分开——每轮开始先清空并搬回停机位，
  // 只有这一轮真的有产出时，渲染函数才把它重新挂进新消息并填充。宁可空着，也不让旧内容跟过来。
  function clearDeliveryNodes() {
    const board = $('candidate-board');
    const card = $('draft-card');
    const images = $('image-results');
    const draftImages = $('draft-images');
    const park = $('delivery-park');
    if (images) { images.hidden = true; images.innerHTML = ''; }
    if (draftImages) draftImages.innerHTML = '';
    if (board) { board.hidden = true; board.innerHTML = ''; }
    if (card) {
      card.hidden = true;
      ['draft-title', 'draft-meta', 'draft-body', 'draft-cta', 'draft-eyebrow'].forEach((id) => {
        const node = $(id);
        if (node) node.textContent = '';
      });
      const tags = $('draft-tags');
      if (tags) tags.innerHTML = '';
      const state = $('draft-state');
      if (state) { state.textContent = ''; state.className = 'draft-state'; }
    }
    if (park && board) park.appendChild(board);
    if (park && card) park.appendChild(card);
  }

  // 新一轮开始时用：清空共享节点，让上一轮的内容不可能跟到新消息里。
  // 但**不动**"已选中的那一篇"与两个按钮：这一轮如果没有新交付，用户仍然能导出上一轮选中的那篇。
  function beginTurnDelivery() {
    clearDeliveryNodes();
  }

  // 新建 / 切换 / 删除任务时用：整体收回，连选中态与入口一起重置。
  function resetDeliveryNodes() {
    clearDeliveryNodes();
    selectedVariant = null;
    cancelVariantSelect();
    closeVariantZoom();   // 放大浮层不属于任何消息，换任务时必须关掉
    parkDelivery();   // 清 lastDelivery + 把入口收回停机位
  }

  // 新一轮真的产出了交付内容时，旧的选中态作废（新一批选项卡需要重新选）。
  function clearDeliverySelection() {
    selectedVariant = null;
    cancelVariantSelect();
    lastDelivery = {copy: '', imageIds: [], imageUrls: []};
    syncDeliveryEntry();
  }

  // C：入口只在"确实有可交付的文案"时出现；并且被搬到那一轮交付图的最下方。
  function syncDeliveryEntry() {
    const entry = $('delivery-entry');
    if (!entry) return;
    const has = Boolean(selectedVariant && selectedVariant.variant) ||
      Boolean((lastDelivery.copy || '').trim());
    entry.hidden = !has;
  }

  function placeDeliveryEntry(host) {
    const entry = $('delivery-entry');
    if (!entry) return;
    const target = host || lastAssistantMessage();
    if (target && entry.parentNode !== target) target.appendChild(entry);
    syncDeliveryEntry();
  }

  // E：工具痕迹收进默认折叠的「执行过程」。
  // 范围 = 每条消息各自的那份 payload（tool_trace / events），一次渲染对应一个 <details>，
  // 不靠 DOM 位置猜，所以上一条消息的痕迹不会混进来。折叠态交给原生 <details>，
  // 不用任何临时变量，重渲染/回放都不会丢。
  function toolTraceHtml(steps, unit) {
    const rows = (steps || []).filter(Boolean);
    if (!rows.length) return '';
    const names = [];
    rows.forEach((step) => {
      const name = step.name || '';
      if (name && names.indexOf(name) < 0) names.push(name);
    });
    // 次数就是本次渲染拿到的步骤条数（真实计数，不是写死的常量）。
    const label = '执行过程 · ' + rows.length + ' 次' + (unit || '工具调用') +
      (names.length ? ' · ' + names.join(' / ') : '');
    const lines = rows.map((step) => {
      const state = step.ok === true ? 'done' : (step.ok === false ? 'warn' : '');
      const stateText = step.ok === true ? '成功' : (step.ok === false ? '失败' : '');
      // 后端成功的 summary 就是字面量 "ok"，别把它当描述显示；失败时 summary 才是错误码/原因。
      const raw = (step.summary || '').trim();
      const summary = raw.toLowerCase() === 'ok' ? '' : raw;
      const detail = [stateText, summary].filter(Boolean).join(' · ');
      return '<div class="trace-line' + (state ? ' ' + state : '') + '">' +
        '<span class="trace-name">' + escapeHtml(step.name || '步骤') + '</span>' +
        (detail ? '<span class="trace-detail">' + escapeHtml(detail) + '</span>' : '') +
        '</div>';
    }).join('');
    return '<details class="tool-trace"><summary class="trace-summary">' +
      escapeHtml(label) + '</summary><div class="trace-list">' + lines + '</div></details>';
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
        resetDeliveryNodes();
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
    // 清空并搬回上一轮的交付节点，新任务里绝不带上一个任务的卡片内容。
    resetDeliveryNodes();
    resetStudioSession();
    chatLog.innerHTML = WELCOME_HTML;
    optionsWrap.hidden = true;
    optionsWrap.innerHTML = '';
    quickActions.hidden = true;
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
      const replayVariants = variantsFrom(conversation);
      if (!visible.length && !(conversation.artifacts || []).length && !replayVariants.length) {
        toast('这个任务还没有内容');
        return;
      }
      sessionId = id;
      lastResult = null;
      loadSurfaceLock();
      lastUserText = [...visible].reverse().find(item => item.role === 'user')?.content || '';
      resetDeliveryNodes();   // 回放前先清空共享节点，避免上一个任务的交付卡混进来
      resetStudioSession();   // 工作室里绑定的材料属于任务，切任务必须作废
      chatLog.innerHTML = '';
      visible.forEach(item => addMessage(item.role === 'user' ? 'operator' : 'assistant',
        '<p>' + escapeHtml(item.content || '').replace(/\n/g, '<br>') + '</p>'));
      const task = conversation.task || {};
      // The autonomous runner has no guided "stage"; render whatever the task actually
      // holds instead of gating on stage names only the legacy runner produces.
      if (replayVariants.length) {
        // 三篇文案（新流程）：回放到最后一条 assistant 消息里，交付入口跟着到图片下方。
        renderVariants(conversation, lastAssistantMessage());
      } else if (task.candidates && task.candidates.length) {
        renderV2Candidates(task.candidates);
      } else if (task.plan && !(conversation.artifacts || []).length) {
        renderV2Plan(task.plan, task.selected);
      }
      if (!replayVariants.length && (conversation.artifacts || []).length) {
        renderV2Artifact(conversation);
      }
      if (!replayVariants.length && task.images && task.images.length) renderV2Images(task.images);
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
        '<span class="trace-step">skill · 文案方向</span>' +
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
    return toolTraceHtml(trace.map(step => ({
      name: step.tool, summary: step.summary, ok: step.ok,
    })), '工具调用');
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
    return toolTraceHtml(rows.map(item => ({
      name: item.tool || '', summary: item.summary || '', ok: item.ok,
    })), '工具调用');
  }

  // 本轮到底成功跑过哪些工具：只有 LangGraph 自主 runner 会逐轮上报 tool_finished，
  // 其他 runner 返回 null，表示"判断不了"，调用方沿用旧行为（不做额外限制）。
  // 用它来区分"这一轮的产出"与"从 checkpoint state 带过来的上一轮字段"。
  function roundToolGate(payload) {
    const version = (payload && payload.runner_version) ||
      ((payload && payload.usage) || {}).runner_version || '';
    if (String(version).indexOf('langgraph') !== 0) return null;
    return (Array.isArray(payload.events) ? payload.events : [])
      .filter(item => item && item.type === 'tool_finished' && item.ok !== false)
      .map(item => item.tool || '');
  }


  // B：交付卡整体搬进这条 assistant 消息，标题/正文/操作按钮/生成图片都跟着进对话流。
  function renderV2Artifact(conversation, messageEl) {
    const artifacts = (conversation && conversation.artifacts) || [];
    const artifact = artifacts[0];
    if (!artifact || !artifact.content) return;
    clearDeliverySelection();   // 新一轮交付：旧的选中态作废
    $('image-results').hidden = true;
    $('image-results').innerHTML = '';
    const content = artifact.content;
    const board = $('candidate-board');
    const card = attachToMessage($('draft-card'), messageEl);
    if (!card) return;
    if (board) board.hidden = true;
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
    // 旧的「去图文工作室 →」锚点（另开 /studio/workbench 页面）已删除：
    // 与新增的 #go-studio 按钮功能重复，用户要求只保留一个入口。
    // #go-studio 走 switchView('studio')，是应用内切换且会自动带入文案与图片。
    const studioHost = $('draft-studio');
    if (studioHost) { studioHost.innerHTML = ''; studioHost.hidden = true; }
    // 7 页图文（页面卡片 / ZIP）已按用户要求整体删除，这里不再渲染任何页面结构。
    // D：记住这次交付的文案，供「去图文工作室」带入（图片身份由本轮 renderV2Images 补上，
    // 先清空上一轮的图片，避免这一轮没有配图时把旧图片当成这次的交付）。
    lastDelivery.copy = [content.title, content.body,
                         content.cta ? '行动引导：' + content.cta : '',
                         (content.tags || []).map(tag => '#' + tag).join(' ')]
                        .filter(Boolean).join('\n\n');
    lastDelivery.imageIds = [];
    lastDelivery.imageUrls = [];
    // 交付入口跟着这条消息走，保证按钮就在这一轮交付内容的下方。
    placeDeliveryEntry(card.parentNode);
    switchTab('plan');
    chatLog.scrollTop = chatLog.scrollHeight;
    loadTasks();
  }

  // A：候选不再渲染成卡片网格，而是 assistant 消息下方一行可换行的 pill。
  // 点击行为完全沿用原来的 data-pick / data-select（事件仍绑在 #candidate-board 上）。
  function renderV2Candidates(candidates, messageEl) {
    const board = $('candidate-board');
    if (!board) return;
    if (!candidates || !candidates.length) { board.hidden = true; syncDeliveryEntry(); return; }
    const card = $('draft-card');
    if (card) card.hidden = true;
    attachToMessage(board, messageEl);
    board.hidden = false;
    // 括号里放真实差异描述（candidate.reason 就是知识库算出的关键差异，如「约 1.31 kg，60 Wh 电池…」），
    // 没有描述时只显示型号名，不编造。
    board.innerHTML = '<div class="candidate-pills">' + candidates.map(candidate => {
      const name = candidate.model || '';
      const meta = candidate.reason || (candidate.facts || []).join('，');
      return '<button type="button" class="candidate-pill" data-pick="' + escapeHtml(name) + '"' +
        ' title="' + escapeHtml(meta || '知识库现有型号') + '">' + escapeHtml(name) +
        (meta ? '（' + escapeHtml(meta) + '）' : '') + '</button>';
    }).join('') + '</div>';
    syncDeliveryEntry();
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  function renderV2Plan(plan, selected, messageEl) {
    if (!plan) return;
    const board = $('candidate-board');
    if (board) board.hidden = true;
    const card = attachToMessage($('draft-card'), messageEl);
    if (!card) return;
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
    // 「方案确认」还不是交付文案，所以不显示「去图文工作室」。
    lastDelivery.copy = '';
    lastDelivery.imageIds = [];
    lastDelivery.imageUrls = [];
    syncDeliveryEntry();
    switchTab('plan');
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  // B：交付图片（知识库原图 / 由对话生成）显示在 #draft-card 内，卡片已进入对话流；
  // 同时记下图片身份，供「去图文工作室」自动设为参考图。
  function renderV2Images(images) {
    const host = $('image-results');
    if (!host) return;
    // 没有配图时必须**清空内容**而不只是隐藏：否则上一轮的图片会留在共享节点里，
    // 卡片被搬到新消息时旧图跟着走（Bug#1 的同一根因）。
    if (!images || !images.length) { host.hidden = true; host.innerHTML = ''; return; }
    // 图片在 #draft-card 内。若这张卡还没进对话流（本轮没有 artifact / plan），
    // 就现在挂到当前 assistant 消息里，保证图片真的出现在对话里而不是留在停机位。
    const card = $('draft-card');
    if (card && card.parentNode === $('delivery-park')) {
      attachToMessage(card);
      card.hidden = false;
    }
    host.hidden = false;
    host.innerHTML = images.map(image =>
      '<figure><img src="' + escapeHtml(image.access_url || '') + '" alt="' +
      escapeHtml(image.caption || '知识库产品原图') + '" loading="lazy" data-image-id="' +
      escapeHtml(image.image_id || '') + '" title="点击查看原图"><figcaption>' +
      escapeHtml(image.caption || '知识库原图') + '</figcaption></figure>').join('');
    lastDelivery.imageIds = images.map(image => image.image_id).filter(Boolean);
    lastDelivery.imageUrls = images.map(image => image.access_url).filter(Boolean);
    syncDeliveryEntry();
  }

  // ===================== 三篇文案（新流程核心交互）=====================
  // 数据契约（后端并行实现，字段缺失一律安全退化）：
  //   variants: [{variant_id, label, title, body, tags, models}]
  //   material: {audience, platform, scenario, campaign, models:[{product_id, model}], hotspot}
  //   images:   [{image_id, access_url, caption}]
  // 只渲染后端真的给回来的东西：缺 variants 就不走这条渲染路径，绝不伪造。
  function asList(value) {
    return Array.isArray(value) ? value.filter(Boolean) : [];
  }

  function variantsFrom(source) {
    if (!source) return [];
    const direct = asList(source.variants);
    if (direct.length) return direct;
    const task = source.task || {};
    if (asList(task.variants).length) return asList(task.variants);
    const material = source.material || {};
    if (asList(material.variants).length) return asList(material.variants);
    return [];
  }

  function imagesFrom(source) {
    if (!source) return [];
    const direct = asList(source.images);
    if (direct.length) return direct;
    const material = source.material || {};
    if (asList(material.images).length) return asList(material.images);
    const task = source.task || {};
    return asList(task.images);
  }

  function materialFrom(source) {
    if (!source) return {};
    return source.material || (source.task && source.task.material) || {};
  }

  function variantCopyText(variant) {
    const v = variant || {};
    return [v.title, v.body, (v.tags || []).map(tag => '#' + tag).join(' ')]
      .filter(Boolean).join('\n\n');
  }

  // variant.models 是 product_id，material.models 才带可读型号名；能对上就用名字，
  // 对不上就显示 id —— 不编造型号名。
  function variantModelNames(variant, material) {
    const rows = asList(material && material.models);
    return ((variant && variant.models) || []).map((id) => {
      const hit = rows.find(row => row && (row.product_id === id || row.model === id));
      return (hit && hit.model) || id;
    });
  }

  function materialSummaryHtml(material) {
    const m = material || {};
    const models = asList(m.models).map(row => row && (row.model || row.product_id)).filter(Boolean);
    const hotspot = m.hotspot || {};
    const bits = [
      m.audience ? '人群：' + m.audience : '',
      m.platform ? '平台：' + (SURFACE_LABELS[m.platform] || m.platform) : '',
      m.scenario ? '场景：' + m.scenario : '',
      m.campaign ? '活动：' + m.campaign : '',
      models.length ? '型号：' + models.join(' / ') : '',
      hotspot.title ? '热点：' + hotspot.title : '',
    ].filter(Boolean);
    if (!bits.length) return '';
    return '<div class="variant-material">本次任务资料 · <span>' +
      bits.map(escapeHtml).join('</span> · <span>') + '</span></div>';
  }

  // 三篇文案渲染成三个"不大"的选项卡；下方是该型号的知识库产品原图；
  // 图片最下方是 #go-studio / #export-delivery 两个按钮。
  // 图片按契约取 images[].access_url；缺 images 时就不画图，不编造。
  function renderVariants(source, messageEl) {
    const variants = variantsFrom(source);
    if (!variants.length) return false;
    const images = imagesFrom(source);
    const material = materialFrom(source);
    const host = messageEl || lastAssistantMessage() || addMessage('assistant', '');
    // 新一批选项卡 = 需要重新选：上一轮选中的那篇在这里作废。
    clearDeliverySelection();
    const board = document.createElement('section');
    board.className = 'variant-board';
    board.__variants = variants;
    board.__images = images;
    board.__material = material;
    board.__selected = null;
    const labelOf = (variant, index) => variant.label || ('方向' + String.fromCharCode(65 + index));
    const tabs = variants.map((variant, index) => {
      const names = variantModelNames(variant, material);
      return '<button type="button" class="variant-tab" data-variant-index="' + index +
        '" title="单击选中 · 双击查看全文">' +
        '<span class="variant-tab-label">' + escapeHtml(labelOf(variant, index)) + '</span>' +
        '<span class="variant-tab-title">' + escapeHtml(variant.title || '（无标题）') + '</span>' +
        (names.length ? '<span class="variant-tab-models">' + escapeHtml(names.join(' / ')) + '</span>' : '') +
        '</button>';
    }).join('');
    const figures = images.map(image =>
      '<figure><img src="' + escapeHtml(image.access_url || '') + '" alt="' +
      escapeHtml(image.caption || '知识库产品原图') + '" loading="lazy" data-image-id="' +
      escapeHtml(image.image_id || '') + '" title="点击查看原图"><figcaption>' +
      escapeHtml(image.caption || '知识库原图') + '</figcaption></figure>').join('');
    board.innerHTML =
      '<div class="variant-board-head"><span class="variant-board-title">' +
      variants.length + ' 篇文案</span>' +
      '<span class="variant-board-hint">单击选中要交付的那篇 · 双击看全文</span></div>' +
      '<div class="variant-tabs">' + tabs + '</div>' +
      materialSummaryHtml(material) +
      (figures ? '<div class="variant-images">' + figures + '</div>' : '');
    // 单击/双击冲突：单击延后 VARIANT_CLICK_DELAY 才执行选中；
    // 期间收到 dblclick 就取消这次选中，直接放大。
    board.addEventListener('click', (event) => {
      const tab = event.target.closest('.variant-tab');
      if (tab) scheduleVariantSelect(board, tab);
    });
    board.addEventListener('dblclick', (event) => {
      const tab = event.target.closest('.variant-tab');
      if (!tab) return;
      cancelVariantSelect();
      // 慢速双击：第一次的选中可能已经生效，这里一并撤销，避免"想放大却被选中"。
      if (board.__selected === Number(tab.dataset.variantIndex)) deselectVariant(board);
      openVariantZoom(board, tab);
    });
    host.appendChild(board);
    placeDeliveryEntry(board);   // 两个按钮放在图片最下方
    chatLog.scrollTop = chatLog.scrollHeight;
    return true;
  }

  function scheduleVariantSelect(board, tab) {
    variantPending = {board, tab};
    if (variantClickTimer) clearTimeout(variantClickTimer);
    variantClickTimer = setTimeout(() => {
      variantClickTimer = null;
      const pending = variantPending;
      variantPending = null;
      if (pending) selectVariant(pending.board, pending.tab);
    }, VARIANT_CLICK_DELAY);
  }

  function cancelVariantSelect() {
    if (variantClickTimer) { clearTimeout(variantClickTimer); variantClickTimer = null; }
    variantPending = null;
  }

  function selectVariant(board, tab) {
    const index = Number(tab.dataset.variantIndex);
    const variant = (board.__variants || [])[index];
    if (!variant) return;
    board.__selected = index;
    board.querySelectorAll('.variant-tab').forEach((item) => {
      item.classList.toggle('active', item === tab);
    });
    // 只有"选中的这一篇"会被工作室与导出消费。
    selectedVariant = {variant, images: board.__images || [],
                       material: board.__material || {}, board};
    lastDelivery.copy = '';
    lastDelivery.imageIds = [];
    lastDelivery.imageUrls = [];
    syncDeliveryEntry();
    toast('已选中「' + (variant.label || ('方向' + (index + 1))) + '」');
  }

  function deselectVariant(board) {
    board.__selected = null;
    board.querySelectorAll('.variant-tab').forEach((item) => item.classList.remove('active'));
    if (selectedVariant && selectedVariant.board === board) selectedVariant = null;
    syncDeliveryEntry();
  }

  // 放大态：全局单例浮层，内容每次打开时重写，隐藏时不显示任何内容（不会留上一轮的残留）。
  function ensureVariantZoom() {
    if (variantZoomEl && document.body.contains(variantZoomEl)) return variantZoomEl;
    const overlay = document.createElement('div');
    overlay.className = 'variant-zoom';
    overlay.id = 'variant-zoom';
    overlay.hidden = true;
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', '文案全文');
    overlay.innerHTML =
      '<div class="variant-zoom-card">' +
      '<div class="variant-zoom-head"><span class="variant-zoom-label"></span>' +
      '<h3 class="variant-zoom-title"></h3>' +
      '<button type="button" class="variant-zoom-close" id="variant-zoom-close" ' +
      'aria-label="关闭">×</button></div>' +
      '<div class="variant-zoom-body"></div>' +
      '<div class="variant-zoom-foot"><div class="variant-zoom-tags"></div>' +
      '<div class="variant-zoom-models"></div></div>' +
      '</div>';
    document.body.appendChild(overlay);
    overlay.addEventListener('click', (event) => {
      if (event.target === overlay || event.target.closest('#variant-zoom-close')) closeVariantZoom();
    });
    variantZoomEl = overlay;
    return overlay;
  }

  function openVariantZoom(board, tab) {
    const index = Number(tab.dataset.variantIndex);
    const variant = (board.__variants || [])[index] || {};
    const material = board.__material || {};
    const overlay = ensureVariantZoom();
    overlay.querySelector('.variant-zoom-label').textContent =
      variant.label || ('方向' + (index + 1));
    overlay.querySelector('.variant-zoom-title').textContent = variant.title || '（无标题）';
    overlay.querySelector('.variant-zoom-body').textContent = variant.body || '';
    overlay.querySelector('.variant-zoom-tags').innerHTML =
      (variant.tags || []).map(tag => '<span>#' + escapeHtml(tag) + '</span>').join('');
    overlay.querySelector('.variant-zoom-models').innerHTML =
      variantModelNames(variant, material).map(name =>
        '<span class="variant-model">' + escapeHtml(name) + '</span>').join('');
    overlay.hidden = false;
    const close = overlay.querySelector('#variant-zoom-close');
    if (close) close.focus();
  }

  function closeVariantZoom() {
    if (variantZoomEl) variantZoomEl.hidden = true;
  }

  // ===================== 一键导出（不引任何库）=====================
  function safeFileName(text) {
    return String(text || '').replace(/[\\/:*?"<>|\s]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);
  }

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  // 文案与对应原图一起下载：一个 .txt（标题 / 正文 / 标签 / 任务资料 / 图片说明）
  // + 每张原图各一个文件。为了 zip 引库不值得（规格允许下载两个文件）。
  async function exportDelivery() {
    const picked = selectedVariant;
    if (!picked || !picked.variant) { toast('请先选一篇文案'); return; }
    const variant = picked.variant;
    const images = picked.images || [];
    const material = picked.material || {};
    const models = variantModelNames(variant, material);
    const lines = [variant.title || '（无标题）', '', variant.body || '', '',
                   (variant.tags || []).map(tag => '#' + tag).join(' ')];
    if (models.length) lines.push('', '关联型号：' + models.join(' / '));
    const bits = [
      material.audience ? '人群：' + material.audience : '',
      material.platform ? '平台：' + (SURFACE_LABELS[material.platform] || material.platform) : '',
      material.scenario ? '场景：' + material.scenario : '',
      material.campaign ? '活动：' + material.campaign : '',
      (material.hotspot && material.hotspot.title) ? '热点：' + material.hotspot.title : '',
    ].filter(Boolean);
    if (bits.length) lines.push('', '本次任务资料：' + bits.join('；'));
    if (images.length) {
      lines.push('', '附：该型号的知识库产品原图（说明 / 来源地址）');
      images.forEach((image, index) => {
        lines.push('- ' + (image.caption || image.image_id || ('原图 ' + (index + 1))) +
          '：' + (image.access_url || ''));
      });
    }
    const stamp = new Date().toISOString().slice(0, 10);
    const base = safeFileName(variant.label || variant.title) || '文案';
    const filename = '华为PC-' + base + '-' + stamp + '.txt';
    downloadBlob(new Blob([lines.join('\n')], {type: 'text/plain;charset=utf-8'}), filename);
    let okImages = 0;
    for (let index = 0; index < images.length; index += 1) {
      const url = images[index].access_url;
      if (!url) continue;
      try {
        const response = await fetch(url);
        if (!response.ok) throw new Error('image');
        const blob = await response.blob();
        const ext = (blob.type || '').indexOf('png') >= 0 ? '.png'
          : (blob.type || '').indexOf('webp') >= 0 ? '.webp' : '.jpg';
        downloadBlob(blob, filename.replace(/\.txt$/, '') + '-原图' + (index + 1) + ext);
        okImages += 1;
      } catch (error) {
        // 取不到就如实说（地址已写进文案文件），不假装成功。
      }
    }
    const failed = images.length - okImages;
    if (!images.length) toast('已下载文案（这一轮没有原图）');
    else if (!failed) toast('已下载：文案 + ' + okImages + ' 张原图（浏览器若拦截多文件下载，请允许）');
    else toast('已下载文案；' + failed + ' 张原图取不到，地址已写进文案文件');
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
      // 这一轮开始前先清空共享交付节点：这一轮没有产出时，绝不把上一轮的内容带进新消息。
      beginTurnDelivery();
      const messageEl = addMessage('assistant', '<p>' +
        escapeHtml(data.reply || '已完成').replace(/\n/g, '<br>') + '</p>' + eventTrace(data.events));
      renderV2Actions(messageEl, data.actions);
      renderSurfaceSuggestion(messageEl, data.surface_suggestion);
      // Bug#1/#2 的另一半根因：run 的 payload 里 images / candidates / artifact 会从 checkpoint
      // 的 state 带过来，所以"这一轮没取图"时 data.images 仍可能是上一轮的图。
      // 用本轮 run 自己的 tool_finished 事件判断这一轮到底产出了什么，避免旧图/旧 pill 重复出现。
      const gate = roundToolGate(data);
      const roundUsed = (names) => gate === null ||
        gate.some(name => names.indexOf(name) >= 0);
      // 新流程：三篇文案（variants）+ 该型号知识库原图（images）。
      // 字段缺失／后端还没就绪时，variantsFrom 返回空数组，直接走原来的渲染路径。
      // 选项卡下方的原图属于"这次交付的那个型号"，按契约直接取 data.images；
      // 而下面的旧版交付卡只在"本轮真的取过图 / 真的存过交付物"时才渲染，
      // 避免上一轮的卡片被重新贴到新消息里（规格第 7 节 bug#1/#2）。
      const imagesThisRound = roundUsed(['find_product_images', 'render_visuals']);
      const roundImages = imagesThisRound ? asList(data.images) : [];
      let usedVariants = renderVariants(data, messageEl);
      if (!usedVariants && roundUsed(['search_products']) && data.candidates && data.candidates.length) {
        renderV2Candidates(data.candidates, messageEl);
      } else if (!usedVariants) {
        // 只有这一轮真的 save_artifact 时，才把会话里的最新交付拉过来渲染。
        if (roundUsed(['save_artifact'])) {
          const conversation = await fetch('/api/v2/conversations/' + sessionId).then(r => r.json());
          usedVariants = renderVariants(conversation, messageEl);
          if (!usedVariants && conversation.artifacts && conversation.artifacts.length) {
            renderV2Artifact(conversation, messageEl);
          }
        } else if (data.plan) {
          renderV2Plan(data.plan, data.selected, messageEl);
        }
      }
      // variants 路径的图片已经画在选项卡下方，不能再往旧的 #draft-card 里塞一遍。
      if (!usedVariants && roundImages.length) renderV2Images(roundImages);
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
    if (result.draft) steps.push(['skill · 文案方案', 'done']);
    if (result.compliance_report) {
      steps.push(['skill · 合规扫描', result.compliance_report.passed ? 'done' : 'warn']);
    }
    if (result.can_publish) steps.push(['gate · 人工确认', 'done']);
    // 旧流程的合成步骤也折叠起来，避免和工具痕迹一样把回答压住。
    return toolTraceHtml(steps.map(([label, state]) => ({
      name: label, ok: state === 'done' ? true : (state === 'warn' ? false : null),
    })), '步骤');
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
    if (!board) return;
    if (result.draft || !result.candidates || !result.candidates.length) {
      if (!result.candidates || !result.candidates.length) { board.hidden = true; syncDeliveryEntry(); }
      return;
    }
    if (draftCard) draftCard.hidden = true;
    attachToMessage(board);
    board.hidden = false;
    const budgetNames = { not_limited: '不限预算', verified: '预算内已核实', unverified: '价格待核实' };
    // 旧流程同样渲染成 pill，而不是卡片网格。
    board.innerHTML = '<div class="candidate-pills">' + result.candidates.map((candidate, index) => {
      const selected = result.selected && result.selected.candidate_id === candidate.candidate_id;
      const dims = (candidate.reasons || []).slice(0, 3)
        .map(reason => reason.dimension).filter(Boolean).join('，');
      const meta = dims || budgetNames[candidate.budget_status] || candidate.budget_status || '价格待核实';
      const price = candidate.current_price
        ? '¥' + candidate.current_price.amount + ' · ' + candidate.current_price.configuration
        : '当前有效价格待核实';
      return '<button type="button" class="candidate-pill' + (selected ? ' selected' : '') +
        '" data-select="' + (index + 1) + '" title="' + escapeHtml(meta + '｜' + price) + '">' +
        escapeHtml(candidate.model_name) + (meta ? '（' + escapeHtml(meta) + '）' : '') + '</button>';
    }).join('') + '</div>';
    syncDeliveryEntry();
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  function renderDraft(result) {
    const card = $('draft-card');
    const board = $('candidate-board');
    const draft = result.draft;
    if (!draft) {
      if (card) card.hidden = true;
      // 空状态块已按要求删除：没有草稿也没有候选时，对话区保持只有对话本身。
      if (board && (!result.candidates || !result.candidates.length)) board.hidden = true;
      syncDeliveryEntry();
      return;
    }
    if (board) board.hidden = true;
    attachToMessage(card);
    if (!card) return;
    card.hidden = false;
    clearDeliverySelection();   // 新一轮交付：旧的选中态作废
    const slots = result.slots || {};
    const platform = PLATFORM_NAMES[draft.platform] || draft.platform_name || '小红书';
    $('draft-eyebrow').textContent = platform + ' · 图文方案';
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
      '<img src="' + escapeHtml(image.access_url) + '" alt="' + escapeHtml(image.caption) + '" title="点击查看原图" data-image-id="' +
      escapeHtml(image.image_id || '') + '" loading="lazy">').join('');
    // D：交付文案与图片身份，供「去图文工作室」带入。
    lastDelivery.copy = [draft.title, draft.body, draft.cta ? '行动引导：' + draft.cta : '',
                         (draft.tags || []).map(tag => '#' + tag).join(' ')]
                        .filter(Boolean).join('\n\n');
    lastDelivery.imageIds = images.map(image => image.image_id).filter(Boolean);
    lastDelivery.imageUrls = images.map(image => image.access_url).filter(Boolean);
    // 交付入口跟着这条消息（老 v1 路径也保持"按钮在交付内容下方"）。
    placeDeliveryEntry(card.parentNode);
    const exportLink = $('export-link');
    if (result.can_publish && sessionId) {
      exportLink.href = '/api/v1/conversations/' + sessionId + '/export?format=markdown';
      exportLink.hidden = false;
    } else {
      exportLink.hidden = true;
    }
    // 7 页图文的「下载六页图片 ZIP / 页面卡片」已按用户要求整体删除。
    const key = (draft.title || '') + '|' + (draft.body || '').slice(0, 20);
    if (key !== lastCoverKey) {
      lastCoverKey = key;
    }
  }

  // 7 页图文的 renderPages() 已按用户要求整体删除（连同 #pages-grid / #pages-empty /
  // #pages-tools / ZIP 下载链接）。这里保留一个空注释位，避免调用点被误加回来。

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
    const noteNode = $('hot-source-note');
    if (noteNode) noteNode.textContent = note || '';
    const wizard = optionsWrap.querySelector('.wizard');
    if (wizard && wizard.dataset.field === 'objective' && lastQuestions.length) {
      renderOptions(lastQuestions);
    }
  }

  async function loadHotspots(slots) {
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
    if (hot) { renderHotModeTabs(); loadHotMode(hotMode === 'topics' ? 'topics' : hotMode); }
    if (studio) loadStudio(false);
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
    const list = $('hot-list');
    // 只有真的点刷新才提示"正在搜索"；进出板块读的是服务端缓存，不该让人等
    if (force) {
      if (note) note.textContent = '正在联网搜索并整理…（约 10–60 秒）';
      if (list) list.innerHTML = '<p class="hint">搜索中…</p>';
    }
    try {
      const response = force
        ? await fetch('/api/v1/hot-topics/refresh', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source: 'search' }),
          })
        : await fetch('/api/v1/hot-topics');
      const data = await response.json();
      renderOrganizedTopics(data);
    } catch (error) {
      if (note) note.textContent = '加载失败：' + error;
      if (list) list.innerHTML = '<p class="hint">加载失败，请点刷新重试。</p>';
    }
  }

  // 热点「参考到对话」：热点进入对话（可见记录）+ 补进本次任务资料。
  // 不抓链接内容——服务端抓小红书需要登录态，会撞风控（见 docs/15）。
  let organizedCache = [];
  // 已参考的热点标题：只用于界面显示"已参考"；真正的状态以服务端返回为准。
  const referencedHotspots = new Set();

  function hotspotSourceLabel() {
    if (hotMode === 'topics') return '热梗（联网搜索）';
    const hit = CHART_PLATFORMS.find(item => item[0] === hotMode);
    return hit ? hit[1] : hotMode;
  }

  // 依次尝试主路由与后端提供的兜底别名；两者都失败才算失败，绝不假装成功。
  async function postHotspotMaterial(payload) {
    const paths = ['/material/hotspot', '/hotspot'];
    let lastMessage = '';
    for (let index = 0; index < paths.length; index += 1) {
      let response = null;
      try {
        response = await fetch('/api/v2/conversations/' + sessionId + paths[index], {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload),
        });
      } catch (error) {
        lastMessage = '网络错误';
        continue;
      }
      let data = null;
      try { data = await response.json(); } catch (error) { data = null; }
      if (response.ok) return {ok: true, data: data || {}};
      if (response.status !== 404) {
        lastMessage = (data && (data.message || data.error)) || ('HTTP ' + response.status);
        return {ok: false, message: lastMessage};
      }
      lastMessage = (data && (data.message || data.error)) || '接口不存在（404）';
    }
    return {ok: false, message: lastMessage || '并入任务资料失败'};
  }

  async function referenceHotspot(payload) {
    const title = (payload && payload.title) || '';
    if (!title) return;
    if (!sessionId) { toast('先在对话里建一个任务，再把热点参考进去'); return; }
    // 热点先"进入对话"：作为一条可见的用户侧记录，用户能看见这次参考资料是什么。
    switchView('chat');
    addMessage('operator', '<p>参考热点：' + escapeHtml(title) +
      (payload.source ? '（' + escapeHtml(payload.source) + '）' : '') + '</p>');
    const result = await postHotspotMaterial(payload);
    if (!result.ok) {
      addMessage('assistant', '<p class="hint">热点已记在这条对话里，但并入本次任务资料失败：' +
        escapeHtml(result.message || '接口未就绪') + '。</p>');
      toast('并入任务资料失败');
      return;
    }
    // 以服务端返回为准标记"已参考"。
    referencedHotspots.add(title);
    const material = (result.data && result.data.material) || {};
    const hotspot = (result.data && result.data.hotspot) || material.hotspot || {};
    refreshReferencedButtons();
    const bits = [material.audience ? '人群 ' + material.audience : '',
                  material.scenario ? '场景 ' + material.scenario : '',
                  // material.hotspot 始终带 title/source 两个键，无热点时 title 是空串，
                  // 所以这里按 title 是否为空判断，而不是按"键是否存在"。
                  (hotspot.title) ? '热点 ' + hotspot.title : ''].filter(Boolean);
    addMessage('assistant', '<p class="hint">已把热点「' + escapeHtml(title) +
      '」并入本次任务资料，后续文案会结合它。' +
      (bits.length ? '（当前资料：' + escapeHtml(bits.join('、')) + '）' : '') + '</p>');
    toast('已参考到对话');
  }

  // 热点卡片里的「参考到对话」按钮状态：参考过就显示"已参考"，避免重复并入。
  function refreshReferencedButtons() {
    document.querySelectorAll('[data-ref-topic]').forEach((button) => {
      const title = button.getAttribute('data-ref-title') || '';
      const done = referencedHotspots.has(title);
      button.textContent = done ? '已参考' : '参考到对话';
      button.classList.toggle('referenced', done);
      button.disabled = done;
    });
  }

  // 把榜单卡片上的按钮还原成后端约定的 body（title/source/url/kind/gist/intro/keywords）。
  function referenceFromButton(button) {
    const title = button.getAttribute('data-ref-title') || '';
    const rawIndex = button.getAttribute('data-ref-topic');
    const topic = (rawIndex === null || rawIndex === '') ? null : organizedCache[Number(rawIndex)];
    if (topic) {
      referenceHotspot({
        title: topic.topic || title, source: hotspotSourceLabel(),
        url: (topic.links || [])[0] || '', kind: topic.kind || '',
        gist: topic.gist || '', intro: topic.intro || '', keywords: topic.keywords || [],
      });
      return;
    }
    referenceHotspot({
      title: title, source: hotspotSourceLabel(), url: '',
      kind: '', gist: '', intro: '', keywords: [],
    });
  }

  // ===================== 图文工作室 =====================
  const studioState = {refs: [], images: [], recipes: [], models: [], generated: [], loaded: false,
                       pendingCopy: '', pendingRefIds: []};

  // 切换/新建任务时，工作室里与会话绑定的材料必须作废：否则上一个任务的 knowledge base
  // 原图会留在列表里，go-studio 带过来的 image_id 对不上，"对应原图"就到不了位。
  function resetStudioSession() {
    studioState.loaded = false;
    studioState.images = [];
    studioState.refs = [];
    studioState.generated = [];
    const box = $('studio-copy-text');
    if (box) box.value = '';
    const refs = $('studio-refs');
    if (refs) refs.innerHTML = '<span class="hint">从右侧知识库原图里点选参考图</span>';
    const kb = $('studio-kb-grid');
    if (kb) kb.innerHTML = '<p class="hint">本次创作还没有绑定知识库原图。</p>';
  }

  // 配方与模型跟会话无关，必须无条件先加载。
  // 踩过的 bug：原来它们和 materials 挤在同一个 Promise.all 里、且被 `if (!sessionId) return`
  // 挡在前面，于是没有会话时两个下拉是空的——看起来像"点不开的假按钮"。
  async function loadStudioCatalog() {
    const recipeHint = $('studio-recipe-hint');
    const modelChip = $('studio-model-chip');
    try {
      const [recipeData, modelData] = await Promise.all([
        fetch('/api/v1/recipes?approved_only=1').then(r => r.json()),
        fetch('/api/v1/image-models').then(r => r.json()),
      ]);
      studioState.recipes = recipeData.recipes || [];
      studioState.models = modelData.models || [];
    } catch (error) {
      // 失败要有可见的原因，不能停在"加载中…"让人以为还在转
      if (recipeHint) recipeHint.textContent = '配方加载失败：' + error;
      if (modelChip) modelChip.textContent = '模型加载失败';
      return;
    }
    renderStudioRecipes('');
    renderStudioModels();
  }

  async function loadStudio(force) {
    await loadStudioCatalog();
    applyPendingCopy();   // D：对话带入的文案优先，且只消费一次
    if (!sessionId) {
      const grid = $('studio-kb-grid');
      if (grid) grid.innerHTML =
        '<p class="hint">先在对话里建一个任务；本次创作绑定的知识库原图会出现在这里。</p>';
      const hist = $('studio-history-grid');
      if (hist) hist.innerHTML = '<p class="hint">还没有生成记录。</p>';
      return;
    }
    if (studioState.loaded && !force) {
      // 已经加载过：材料不会再拉，但这次从对话带入的参考图要立刻就位。
      // （loadStudio 的提前返回就在这里；pendingRefIds 必须在这条路径上也消费掉，
      //  否则第二次点「图文工作室」就带不进参考图。）
      if (applyPendingRefs()) renderStudioRefs();
      // 带入的文案也要立刻生效：applyPendingCopy() 在最前面，覆盖了提前返回这条路径。
      return;
    }
    try {
      const material = await fetch('/api/v2/conversations/' + sessionId + '/studio')
        .then(r => r.json());
      studioState.images = material.images || [];
      studioState.loaded = true;

      const surface = material.surface || '';
      const chip = $('studio-surface-chip');
      if (chip) chip.textContent = '交付面：' + (surface ? surfaceLabel(surface) : '未锁定');
      renderStudioRecipes(surface);   // 按交付面过滤配方

      const copyBox = $('studio-copy-text');
      // 接口返回排在最后：只有用户没在编辑、也没从对话带入过，才用接口内容填。
      if (copyBox && !copyBox.value) copyBox.value = studioCopyFromMaterial(material.copy);
      renderStudioRecipes(surface);
      renderStudioKb();
      if (applyPendingRefs()) renderStudioRefs();
      renderStudioRefs();
      await loadStudioHistory();
    } catch (error) {
      toast('图文工作室加载失败');
    }
  }

  // 接口返回的交付内容可能是整份 revision.content（dict：title/body/cta/tags），
  // 也可能已经是字符串；统一成可编辑的正文，避免 textarea 里出现 "[object Object]"。
  function studioCopyFromMaterial(copy) {
    const content = copy && copy.content;
    if (!content) return '';
    if (typeof content === 'string') return content;
    return [content.title, content.body, content.cta ? '行动引导：' + content.cta : '',
            (content.tags || []).map(tag => '#' + tag).join(' ')]
           .filter(Boolean).join('\n\n');
  }

  // D：对话带入的文案优先级最高，并且只消费一次。
  // 顺序是「对话带入（pendingCopy）> 用户已经在编辑的 > /studio 接口返回的 copy.content」。
  // 放在 loadStudio 最前面调用：第二次进工作室时 studioState.loaded 会让函数提前 return，
  // 若放在后面，第二次点「去图文工作室」就带不进文案了。
  function applyPendingCopy() {
    const pending = studioState.pendingCopy;
    if (!pending) return;
    studioState.pendingCopy = '';
    const box = $('studio-copy-text');
    if (box) box.value = pending;
    const note = $('studio-copy-note');
    if (note) note.textContent = '文案已从对话带入，可直接编辑；文案优化只改表达、不新增事实。';
  }

  // D：把对话交付图片里带 image_id 的知识库原图自动设为参考图。
  // 两边同源（都来自知识库的 verified_images，image_id 一致），能对上才勾；
  // 对不上的（例如对话里「一键生图」的产物）就不勾，不伪造参考图。
  function applyPendingRefs() {
    const ids = studioState.pendingRefIds || [];
    studioState.pendingRefIds = [];
    if (!ids.length) return 0;
    // 与用户已选的参考图合并（不覆盖手工选择），最多 9 张。
    const picked = studioState.refs.slice();
    (studioState.images || []).forEach((row, index) => {
      if (row && row.image_id && ids.indexOf(row.image_id) >= 0
          && picked.indexOf(index) < 0 && picked.length < 9) picked.push(index);
    });
    if (picked.length === studioState.refs.length) return 0;
    studioState.refs = picked;
    return picked.length;
  }

  function renderStudioModels() {
    const sel = $('studio-model');
    const chip = $('studio-model-chip');
    if (!sel) return;
    const rows = studioState.models || [];
    if (!rows.length) {
      sel.innerHTML = '<option value="">（模型列表为空）</option>';
      if (chip) chip.textContent = '模型列表为空';
      return;
    }
    sel.innerHTML = rows.map(row =>
      '<option value="' + escapeHtml(row.model) + '"' +
      (row.preferred_reference ? ' selected' : '') + '>' +
      escapeHtml(row.model) + ' · ' + escapeHtml(row.status) + '</option>').join('');
    const usable = rows.filter(row => row.supports_reference === true);
    const chosen = rows.find(row => row.preferred_reference) || usable[0] || rows[0] || {};
    if (chip) {
      chip.textContent = usable.length + ' 个可用参考图' +
        (chosen.evidence ? ' · 依据：' + chosen.probed_at + ' 探针' : '');
    }
    if (sel && chosen.model) sel.value = chosen.model;
  }

  // 版式：16 族各 4 个版式。族变了自动刷新版式列表，所以用户只需两步。
  function renderStudioRecipes(surface) {
    const sel = $('studio-recipe');
    const hint = $('studio-recipe-hint');
    if (!sel) return;
    const all = studioState.recipes || [];
    const list = all.filter(row =>
      !surface || !(row.surfaces || []).length || (row.surfaces || []).includes(surface));
    const usable = list.length ? list : all;
    // 即使配方一个都没加载到，也要保留"通用风格"这一项，避免出现空下拉。
    sel.innerHTML = '<option value="">通用风格（不用配方）</option>' + usable.map(row =>
      '<option value="' + escapeHtml(row.recipe_id) + '">' + escapeHtml(row.name) +
      (row.approved ? '' : '（未批准）') + '</option>').join('');
    if (hint) {
      hint.textContent = '已批准 ' + studioState.recipes.length + ' 个' +
        (surface ? '，当前交付面可用 ' + usable.length + ' 个' : '');
    }
  }

  function renderStudioKb() {
    const grid = $('studio-kb-grid');
    const count = $('studio-kb-count');
    if (!grid) return;
    if (count) count.textContent = studioState.images.length + ' 张';
    grid.innerHTML = studioState.images.length
      ? studioState.images.map((row, index) =>
          '<button type="button" class="studio-kb" data-kb="' + index + '" title="' +
          escapeHtml(row.caption || row.image_id || '') + '">' +
          '<img src="' + escapeHtml(row.access_url || row.url || '') + '" alt=""></button>').join('')
      : '<p class="hint">本次创作还没有绑定知识库原图。</p>';
  }

  function renderStudioRefs() {
    const box = $('studio-refs');
    const label = $('studio-ref-count');
    if (label) label.textContent = '已选 ' + studioState.refs.length + ' 张（最多 9 张）';
    document.querySelectorAll('.studio-kb').forEach((btn) => {
      btn.classList.toggle('active', studioState.refs.includes(Number(btn.dataset.kb)));
    });
    if (!box) return;
    box.innerHTML = studioState.refs.length
      ? studioState.refs.map((index) => {
          const row = studioState.images[index] || {};
          return '<span class="studio-ref"><img src="' +
            escapeHtml(row.access_url || row.url || '') + '" alt=""></span>';
        }).join('')
      : '<span class="hint">从右侧知识库原图里点选参考图</span>';
  }

  async function loadStudioHistory() {
    const grid = $('studio-history-grid');
    if (!grid || !sessionId) return;
    try {
      const data = await fetch('/api/v2/conversations/' + sessionId + '/studio/generated')
        .then(r => r.json());
      const rows = data.images || [];
      grid.innerHTML = rows.length ? rows.map((row, index) =>
        '<article class="studio-card">' +
        '<img src="' + escapeHtml(row.url) + '" alt="">' +
        '<div class="studio-card-body">' +
        '<p class="studio-card-prompt">' + escapeHtml(row.prompt || '') + '</p>' +
        '<div class="studio-card-meta"><span>' + escapeHtml(row.model || '') + '</span>' +
        '<span>' + escapeHtml(row.at || '') + '</span></div>' +
        '<div class="studio-card-actions">' +
        '<button type="button" class="studio-btn" data-reuse="' + index + '">复用</button>' +
        '<button type="button" class="studio-btn" data-redraw="' + index + '">重绘</button>' +
        '</div></div></article>').join('')
        : '<p class="hint">还没有生成记录。' + escapeHtml(data.note || '') + '</p>';
      studioState.generated = rows;
    } catch (error) { /* 历史加载失败不影响主流程 */ }
  }

  async function runStudioGenerate() {
    if (!sessionId) { toast('先建一个任务'); return; }
    const prompt = ($('studio-prompt') || {}).value || '';
    if (!prompt.trim()) { toast('请先写提示词'); return; }
    if (!studioState.refs.length) { toast('请先从右侧选一张参考图'); return; }
    const btn = $('studio-generate');
    if (btn) { btn.disabled = true; btn.textContent = '生成中…（约 30–100 秒）'; }
    const note = $('studio-image-note');
    if (note) note.textContent = '正在生成…';
    try {
      // 质量→尺寸：标准用 auto，高清按比例挑一个大尺寸。如实回报最终用的尺寸。
      const count = Number(($('studio-count') || {}).value || 1);
      const quality = ($('studio-quality') || {}).value || 'standard';
      const ratio = ($('studio-ratio') || {}).value || '';
      let size = '';
      if (quality === 'high') {
        size = ratio === 'portrait' ? '1080x1440'
             : ratio === 'landscape' ? '1440x1080' : '1024x1536';
      }
      const payload = {
        prompt: prompt,
        image_id: (studioState.images[studioState.refs[0]] || {}).image_id || '',
        recipe_id: ($('studio-recipe') || {}).value || '',
        ratio: ratio,
        model: ($('studio-model') || {}).value || '',
        count: count,
        size: size,
      };
      const data = await fetch('/api/v2/conversations/' + sessionId + '/studio/image',
        {method: 'POST', headers: {'Content-Type': 'application/json'},
         body: JSON.stringify(payload)}).then(r => r.json());
      if (!data.ok) {
        if (note) note.textContent = data.message || data.error || '生成失败';
        toast(data.error || '生成失败');
        return;
      }
      const used = data.recipe || {};
      if (note) {
        note.textContent = '已生成 ' + ((data.images || []).length) + ' 张（请求 ' +
          (data.requested_count || 1) + ' 张，尺寸 ' + (data.size || 'auto') + '）。' +
          (used.recipe_id
          ? '风格配方：' + (used.recipe_name || used.recipe_id)
          : '未使用配方（' + (used.why_no_recipe || '无已批准配方') + '）') +
          '；生成的是场景图，不是产品实拍。';
      }
      await loadStudioHistory();
    } catch (error) {
      if (note) note.textContent = '生成请求失败';
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '生成图片'; }
    }
  }

  async function optimizeStudioCopy() {
    const box = $('studio-copy-text');
    const note = $('studio-copy-note');
    if (!box || !box.value.trim()) { toast('没有可优化的文案'); return; }
    const btn = $('studio-optimize');
    if (btn) btn.disabled = true;
    if (note) note.textContent = '正在优化…';
    try {
      const data = await fetch('/api/v2/conversations/' + sessionId + '/studio/copy/optimize',
        {method: 'POST', headers: {'Content-Type': 'application/json'},
         body: JSON.stringify({content: box.value})}).then(r => r.json());
      if (!data.ok) { if (note) note.textContent = data.message || '优化失败'; return; }
      box.value = data.optimized;
      if (note) {
        note.textContent = '已优化：' + (data.changes || []).join('；') +
          (data.kept || []).length ? '（保留：' + (data.kept || []).join('；') + '）' : '';
      }
    } catch (error) {
      if (note) note.textContent = '优化请求失败';
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function bindStudio() {
    on('studio-kb-grid', 'click', (event) => {
      const btn = event.target.closest('[data-kb]');
      if (!btn) return;
      const index = Number(btn.dataset.kb);
      const at = studioState.refs.indexOf(index);
      if (at >= 0) studioState.refs.splice(at, 1);
      else if (studioState.refs.length < 9) studioState.refs.push(index);
      else { toast('参考图最多 9 张'); return; }
      renderStudioRefs();
    });
    on('studio-prompt', 'input', () => {
      const box = $('studio-prompt');
      const counter = $('studio-prompt-count');
      if (box && counter) counter.textContent = box.value.length + ' / 15000';
    });
    on('studio-generate', 'click', runStudioGenerate);
    on('studio-optimize', 'click', optimizeStudioCopy);
    on('studio-history-refresh', 'click', loadStudioHistory);
    on('studio-copy', 'click', async () => {
      const box = $('studio-copy-text');
      if (!box || !box.value) { toast('没有可复制的文案'); return; }
      try {
        await navigator.clipboard.writeText(box.value);
        toast('文案已复制');
      } catch (error) {
        box.select(); document.execCommand('copy'); toast('文案已复制');
      }
    });
    on('studio-regen', 'click', () => {
      // 重新生成：把需求带回对话，走 agent 的原流程（不在工作室里另造一套生成逻辑）
      const box = $('studio-copy-text');
      switchView('chat');
      if (input) {
        input.value = '重新生成一版文案。' + (box && box.value ? '\n上一版供参考（可改方向）：\n' + box.value.slice(0, 400) : '');
        input.focus();
      }
      toast('已带回对话，改好需求后发送');
    });
    on('studio-history-grid', 'click', (event) => {
      const reuse = event.target.closest('[data-reuse]');
      const redraw = event.target.closest('[data-redraw]');
      const promptBox = $('studio-prompt');
      if (reuse && promptBox) {
        promptBox.value = studioState.generated[Number(reuse.dataset.reuse)].prompt || '';
        toast('提示词已填入');
      }
      if (redraw && promptBox) {
        promptBox.value = studioState.generated[Number(redraw.dataset.redraw)].prompt || '';
        runStudioGenerate();
      }
    });
  }

  function renderOrganizedTopics(data) {
    const note = $('hot-source-note');
    const list = $('hot-list');
    if (!data || !data.ok) {
      if (note) note.textContent = (data && (data.message || data.error)) || '没有可用的热点清单';
      if (list) list.innerHTML = '<p class="hint">' +
        escapeHtml((data && (data.message || data.error)) || '暂无可展示内容') + '</p>';
      return;
    }
    const src = data.source || {};
    // 联网搜索与读榜单的 source 结构不同：搜索给 result_count/providers，榜单给 available/source。
    // 之前只读榜单字段，界面上出现了「取到 undefined 条」。
    const count = src.available != null ? src.available : (src.result_count || 0);
    const from = src.source || (src.providers || []).join('/') || '联网搜索';
    if (note) {
      note.textContent = '来源：' + from + ' · 取到 ' + count + ' 条'
        + (src.cap_note ? '（' + src.cap_note + '）' : '')
        + ' · 品牌过滤剔除 ' + (data.excluded_by_brand || []).length + ' 条'
        + ' · 整理出 ' + data.topic_count + ' 条热点'
        + (data.cached ? ' · 缓存' : ' · 刚刚生成');
    }
    const topics = data.topics || [];
    organizedCache = topics;
    list.innerHTML = topics.length
      ? topics.map((topic, index) => {
          const done = referencedHotspots.has(topic.topic);
          return '<article class="topic-card" data-topic="' + index + '">' +
          '<div class="topic-head"><span class="topic-kind">' + escapeHtml(topic.kind) + '</span>' +
          '<strong>' + escapeHtml(topic.topic) + '</strong></div>' +
          '<p class="topic-gist">' + escapeHtml(topic.gist) + '</p>' +
          (topic.intro ? '<p class="topic-intro">' + escapeHtml(topic.intro) + '</p>' : '') +
          '<div class="topic-actions">' +
          // 规格：热点榜里每个热点都能「参考到对话」——点一下热点进入对话并补进任务资料。
          '<button type="button" class="chip' + (done ? ' referenced' : '') +
          '" data-ref-topic="' + index + '" data-ref-title="' + escapeAttr(topic.topic) + '"' +
          (done ? ' disabled' : '') + '>' + (done ? '已参考' : '参考到对话') + '</button>' +
          ((topic.links || [])[0]
            ? '<a class="chip" href="' + escapeHtml(topic.links[0]) + '" target="_blank" rel="noopener">在小红书打开 ↗</a>'
            : '') +
          ((topic.keywords || []).length
            ? '<span class="topic-kw">搜索词：' + topic.keywords.map(escapeHtml).join(' ／ ') + '</span>'
            : '') +
          '</div>' +
          ((topic.source_titles || []).length
            ? '<p class="topic-src">原始条目：' + topic.source_titles.map(escapeHtml).join(' ／ ') + '</p>'
            : '') +
          '</article>';
        }).join('')
      : '<p class="hint">这一轮没有整理出好玩的热点（剔除了新闻事件与其他品牌后为空，这是正常结果）。</p>';
  }

  // 热点板块：热梗（联网搜索）+ 各平台原始榜单（抖音/微博/知乎/小红书）
  const CHART_PLATFORMS = [
    ['douyin', '抖音热榜'], ['weibo', '微博热搜'],
    ['zhihu', '知乎热榜'], ['rednote', '小红书热榜'],
  ];
  let hotMode = 'topics';       // topics（热梗）| 某个平台
  let chartCache = {};

  function renderHotModeTabs() {
    const wrap = $('hot-scenario-tabs');
    if (!wrap) return;
    const items = [['topics', '热梗（联网搜索）'], ...CHART_PLATFORMS];
    // 用与对话区交付面完全相同的 pill 样式（复用 .surface-tabs/.surface-btn）。
    // 不能复用 .section-tabs：它有 flex:0 0 44px，在横向容器里会变成 44px 宽，把整行挤成一竖条。
    wrap.innerHTML = items.map(([key, label]) =>
      '<button type="button" class="surface-btn' + (key === hotMode ? ' active' : '') +
      '" data-hot-mode="' + key + '">' + escapeHtml(label) + '</button>').join('');
  }

  async function loadHotMode(mode) {
    hotMode = mode;
    renderHotModeTabs();
    const note = $('hot-source-note');
    const list = $('hot-list');
    if (mode === 'topics') return loadOrganizedTopics(false);
    if (chartCache[mode]) return renderChart(mode, chartCache[mode]);
    if (note) note.textContent = '正在取「' + (CHART_PLATFORMS.find(p => p[0] === mode) || [])[1] + '」…';
    if (list) list.innerHTML = '<p class="hint">加载中…</p>';
    try {
      const data = await fetch('/api/v1/hot-charts?platforms=' + mode).then(r => r.json());
      const board = (data.boards || [])[0] || {};
      chartCache[mode] = data;
      renderChart(mode, data);
    } catch (error) {
      if (note) note.textContent = '取榜单失败';
      if (list) list.innerHTML = '<p class="hint">取榜单失败，请点刷新重试。</p>';
    }
  }

  function renderChart(mode, data) {
    const note = $('hot-source-note');
    const list = $('hot-list');
    const board = (data.boards || [])[0] || {};
    const label = (CHART_PLATFORMS.find(p => p[0] === mode) || [])[1] || mode;
    if (note) {
      note.textContent = board.ok
        ? label + ' · ' + board.count + ' 条在榜原文 · 抓取于 ' + (data.fetched_at || '').slice(11, 16)
        : label + ' · ' + (board.note || '本次没有返回内容');
    }
    if (!list) return;
    list.innerHTML = (board.items || []).length
      ? (board.items || []).map((item) => {
          const done = referencedHotspots.has(item.title);
          return '<article class="topic-card chart-card">' +
          '<div class="topic-head"><span class="topic-kind">#' + escapeHtml(String(item.rank)) + '</span>' +
          '<strong>' + escapeHtml(item.title) + '</strong></div>' +
          (item.hot ? '<p class="topic-gist">热度 ' + escapeHtml(String(item.hot)) + '</p>' : '') +
          '<div class="topic-actions">' +
          // 榜单条目同样是"每个热点都能参考到对话"。
          '<button type="button" class="chip' + (done ? ' referenced' : '') +
          '" data-ref-topic="" data-ref-title="' + escapeAttr(item.title) + '"' +
          (done ? ' disabled' : '') + '>' + (done ? '已参考' : '参考到对话') + '</button>' +
          (item.url ? '<a class="chip" href="' + escapeHtml(item.url) +
            '" target="_blank" rel="noopener">打开原文 ↗</a>' : '') +
          '</div>' +
          '</article>';
        }).join('')
      : '<p class="hint">' + escapeHtml(board.note || '本次没有返回内容。') + '</p>';
  }

  async function loadXhsStatus() {
    if (!button) return;   // 顶栏的小红书按钮已按用户要求删除
    try {
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

  // 工作栏（含「六页图文 / 六页样稿」选项卡与面板）已按用户要求删除。
  // 7 页图文的 renderWorkTabs() / populateSamplePages() 也一并删除，避免半个坏掉的界面。
  // switchTab 保留为空操作：老的 v1 渲染路径仍会调用它，但目标节点已不存在。
  function switchTab(name) {
    const workTabs = $('work-tabs');
    if (!workTabs) return;   // 工作栏已删除
    workTabs.querySelectorAll('.tab').forEach(item => item.classList.toggle('active', item.dataset.tab === name));
    document.querySelectorAll('.tab-panel').forEach(panel => { panel.hidden = true; });
    const panel = $('panel-' + name);
    if (panel) panel.hidden = false;
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

  // 候选 pill 的点击沿用原有行为：事件仍绑在这个节点上（节点只是被搬进消息，监听器不丢）。
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

  // C/D：「图文工作室」= 用现有的 switchView('studio')，切换前把**选中的那一篇**与对应原图
  // 写进 studioState（不是"最新一篇"）。
  on('go-studio', 'click', () => {
    const picked = selectedVariant;
    const copy = picked ? variantCopyText(picked.variant)
      : (lastDelivery.copy || '').trim();
    if (!copy) { toast('请先选一篇文案'); return; }
    studioState.pendingCopy = copy;
    // 只带 image_id：工作室参考图来自 /studio 的 materials.images，两边同源才对得上。
    // 对话里「一键生图」产生的图片没有 image_id、也不在 materials.images 里，
    // 无法自动进参考图或「最近生成」（后者只由工作室自己的生成动作写入）——这里不伪造。
    studioState.pendingRefIds = picked
      ? (picked.images || []).map(image => image.image_id).filter(Boolean)
      : (lastDelivery.imageIds || []).slice();
    switchView('studio');
  });

  // 「一键导出」：下载【选中的那篇文案 + 对应的原图】；未选中时明确提示，不静默失败。
  on('export-delivery', 'click', () => { exportDelivery(); });

  // B：交付图片点击在新标签打开原图（不引库）。
  // 知识库原图的 access_url 是临时令牌、会过期。加载失败时若只留一个圆角空框 + 型号名，
  // 看起来就和候选 pill 一样，用户会以为 agent 又让他重选一次型号。
  // 所以失败必须显式化：隐藏破图、给出可读原因。error 事件不冒泡，必须用捕获阶段。
  chatLog.addEventListener('error', (event) => {
    const img = event.target;
    if (!img || img.tagName !== 'IMG') return;
    if (!img.closest('.draft-images, .image-results, .variant-images')) return;
    const figure = img.closest('figure') || img.parentNode;
    if (figure) figure.classList.add('image-broken');
    img.hidden = true;
    if (figure && !figure.querySelector('.image-broken-note')) {
      const note = document.createElement('span');
      note.className = 'image-broken-note';
      note.textContent = '图片地址已过期，暂时无法显示；点「刷新材料」可重新取图。';
      img.insertAdjacentElement('afterend', note);
    }
  }, true);

  chatLog.addEventListener('click', (event) => {
    const img = event.target && event.target.closest ? event.target.closest('img') : null;
    if (!img || !img.closest('.draft-images, .image-results, .variant-images')) return;
    const src = img.getAttribute('src');
    if (src) window.open(src, '_blank', 'noopener');
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


  on('hot-back', 'click', () => switchView('chat'));
  on('studio-back', 'click', () => switchView('chat'));
  $('hot-refresh').addEventListener('click', () => {
    // 刷新按钮只对"热梗"有意义；平台榜单刷新就重新取源
    if (hotMode === 'topics') return loadOrganizedTopics(true);
    chartCache[hotMode] = null;
    return loadHotMode(hotMode);
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
  bindStudio();
  loadStudioCatalog();   // 配方与模型与会话无关，进页面就先填好
  on('hot-scenario-tabs', 'click', (event) => {
    const button = event.target.closest('[data-hot-mode]');
    if (button) loadHotMode(button.getAttribute('data-hot-mode'));
  });
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
    const use = event.target.closest('[data-ref-topic]');
    if (use) { referenceFromButton(use); return; }
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
    if (!meta) {
      // 放大态用 Esc 关闭（关闭后回到紧凑的选项卡）。
      if (event.key === 'Escape') closeVariantZoom();
      return;
    }
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
  loadTasks();

  window.AgentApp = { getSessionId: () => sessionId, switchView };

  loadKnowledgeBase();
  loadImageStyles();
  loadXhsStatus();
  loadModels();
  input.focus();
})();
