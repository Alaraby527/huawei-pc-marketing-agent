(() => {
  const $ = (id) => document.getElementById(id);
  const chatLog = $('chat-log');
  const form = $('composer');
  const input = $('input');
  const sendBtn = $('send');
  const quickActions = $('quick-actions');
  const optionsWrap = $('options-wrap');
  const platformSwitch = $('platform-switch');
  const stepper = $('stepper');

  let sessionId = null;
  let lastResult = null;
  let selectedPlatform = 'xiaohongshu';
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

  function readTasks() {
    try {
      const stored = JSON.parse(localStorage.getItem(TASKS_KEY) || '[]');
      return Array.isArray(stored) ? stored : [];
    } catch (error) {
      return [];
    }
  }

  function writeTasks(tasks) {
    try {
      localStorage.setItem(TASKS_KEY, JSON.stringify(tasks.slice(0, 20)));
    } catch (error) {
      // Task history is a convenience; the chat still works without storage.
    }
  }

  function relativeTime(value) {
    const seconds = Math.max(0, Math.round((Date.now() - value) / 1000));
    if (seconds < 60) return '刚刚';
    if (seconds < 3600) return Math.round(seconds / 60) + ' 分钟前';
    if (seconds < 86400) return Math.round(seconds / 3600) + ' 小时前';
    return Math.round(seconds / 86400) + ' 天前';
  }

  function saveTask() {
    if (!sessionId) return;
    const existing = readTasks().find(task => task.id === sessionId);
    const title = (lastUserText || (existing && existing.title) || '新任务')
      .replace(/\s+/g, ' ').slice(0, 22) || '新任务';
    const tasks = readTasks().filter(task => task.id !== sessionId);
    tasks.unshift({ id: sessionId, title, updated: Date.now() });
    writeTasks(tasks);
    renderTasks();
  }

  function renderTasks() {
    const wrap = $('task-list');
    const tasks = readTasks();
    if (!tasks.length) {
      wrap.innerHTML = '<p class="hint">暂无任务</p>';
      return;
    }
    wrap.innerHTML = tasks.map(task =>
      '<button type="button" class="task-item' + (task.id === sessionId ? ' active' : '') +
      '" data-task="' + escapeHtml(task.id) + '"><span class="task-title">' + escapeHtml(task.title) +
      '</span><span class="task-time">' + relativeTime(task.updated) + '</span></button>').join('');
  }

  function newTask() {
    sessionId = null;
    lastResult = null;
    lastUserText = '';
    selectedHotIndex = -1;
    chatLog.innerHTML = WELCOME_HTML;
    optionsWrap.hidden = true;
    optionsWrap.innerHTML = '';
    quickActions.hidden = true;
    $('candidate-board').hidden = true;
    $('draft-card').hidden = true;
    $('plan-empty').hidden = false;
    $('cover-result').hidden = true;
    $('publish-state').textContent = '尚未通过核验，不可发布';
    $('publish-state').className = '';
    renderTasks();
    switchView('chat');
    input.value = '';
    sendBtn.disabled = true;
    input.focus();
    toast('已新建任务');
  }

  async function openTask(id) {
    if (!id) return;
    if (id === sessionId) {
      switchView('chat');
      return;
    }
    try {
      const response = await fetch('/api/v1/conversations/' + encodeURIComponent(id) + '/state');
      if (!response.ok) {
        toast('任务已过期，请新建任务');
        return;
      }
      const result = await response.json();
      sessionId = id;
      lastUserText = ((result.slots && result.slots.instructions) || '').split('\n')[0];
      chatLog.innerHTML = WELCOME_HTML;
      (result.history || []).forEach(item => {
        if (item && item.text) {
          addMessage(item.role === 'operator' ? 'operator' : 'assistant',
            '<p>' + escapeHtml(item.text) + '</p>');
        }
      });
      render(result);
      switchView('chat');
    } catch (error) {
      toast('任务加载失败，请重试');
    }
  }

  function setWorkPanel(open) {
    const panel = $('work-panel');
    panel.classList.toggle('collapsed', !open);
    $('work-toggle').setAttribute('aria-expanded', String(open));
    try {
      localStorage.setItem(WORK_PANEL_KEY, open ? '1' : '0');
    } catch (error) {
      // Non-persistent toggle is fine.
    }
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

  function send(message, slotUpdates) {
    if (turnInFlight) return;
    if (message && message.trim()) lastUserText = message.trim();
    if (selectedEngine === 'agent') return agentTurn(message || '');
    const payload = { session_id: sessionId, message: message || '', engine: selectedEngine };
    if (selectedEngine === 'llm' && selectedModel) payload.model = selectedModel;
    if (slotUpdates) payload.slot_updates = slotUpdates;
    return requestTurn(payload);
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
    saveTask();
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
    const empty = $('plan-empty');
    const draftCard = $('draft-card');
    if (result.draft || !result.candidates || !result.candidates.length) {
      if (!result.candidates || !result.candidates.length) board.hidden = true;
      return;
    }
    empty.hidden = true;
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
    const empty = $('plan-empty');
    const board = $('candidate-board');
    const draft = result.draft;
    if (!draft) {
      card.hidden = true;
      if (!result.candidates || !result.candidates.length) empty.hidden = false;
      return;
    }
    empty.hidden = true;
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
      const textModels = data.text || [];
      const defaultModel = data.default || textModels[0] || null;
      select.innerHTML = '<option value="agent">自主 Agent（工具调用）</option>' + textModels.map(model =>
        '<option value="llm:' + escapeHtml(model) + '">' +
        (model === defaultModel ? '默认模型 · ' : '') + escapeHtml(model) + '</option>').join('') +
        '<option value="rule">规则引擎</option>' +
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
      $('xhs-login').textContent = '小红书已连接';
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

  function switchView(view) {
    const hot = view === 'hot';
    const workspace = view === 'workspace';
    $('chat-view').hidden = hot || workspace;
    $('hot-view').hidden = !hot;
    $('workspace-view').hidden = !workspace;
    if (hot) loadHotList();
    if (workspace && window.WorkspaceApp) window.WorkspaceApp.open();
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
          $('xhs-status').textContent = '等待扫码；如果已经扫码，请在手机上点“确认登录”';
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
    try {
      const response = await fetch('/api/v1/knowledge/connection');
      const data = await response.json();
      if (response.ok) {
        const name = data.knowledge_base_name || '';
        badge.textContent = name.includes('本地官方') ? '知识库：本地官方库 v7' : '知识库：已连接 · ' + name;
        badge.title = name;
        badge.className = 'badge ok';
      } else {
        badge.textContent = '知识库：暂不可用';
        badge.className = 'badge err';
      }
    } catch (error) {
      badge.textContent = '知识库：检测失败';
      badge.className = 'badge err';
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
    $('work-tabs').querySelectorAll('.tab').forEach(item => item.classList.toggle('active', item.dataset.tab === name));
    document.querySelectorAll('.tab-panel').forEach(panel => { panel.hidden = true; });
    const panel = $('panel-' + name);
    if (panel) panel.hidden = false;
  }

  function renderWorkTabs(platform) {
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
    const button = event.target.closest('button[data-select]');
    if (!button) return;
    const text = '选' + button.dataset.select;
    addMessage('operator', '<p>' + escapeHtml(text) + '</p>');
    send(text);
  });

  $('plan-empty').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-example]');
    if (!button) return;
    input.value = button.dataset.example;
    form.requestSubmit();
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

  $('hot-search').addEventListener('click', () => switchView('hot'));
  $('workspace-btn').addEventListener('click', () => switchView('workspace'));
  $('hot-back').addEventListener('click', () => switchView('chat'));
  $('hot-refresh').addEventListener('click', () => {
    hotTopics = [];
    currentHotTopics = [];
    lastHotKey = '';
    loadHotList();
  });
  $('hot-connect').addEventListener('click', xhsLogin);

  $('xhs-login').addEventListener('click', xhsLogin);
  $('xhs-refresh').addEventListener('click', xhsLogin);
  $('xhs-fallback').addEventListener('click', () => {
    closeXhsModal();
    switchView('hot');
    toast('已切换到公开热榜参考');
  });
  $('xhs-close').addEventListener('click', closeXhsModal);
  $('xhs-modal').addEventListener('click', (event) => {
    if (event.target.id === 'xhs-modal') closeXhsModal();
  });

  platformSwitch.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-platform]');
    if (!button) return;
    selectedPlatform = button.dataset.platform;
    platformSwitch.querySelectorAll('.platform-btn').forEach(item => item.classList.toggle('active', item === button));
    renderWorkTabs(selectedPlatform);
    send('', { platform: selectedPlatform });
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

  $('work-tabs').addEventListener('click', (event) => {
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
        body: JSON.stringify({ session_id: sessionId, source: source, style: $('image-style').value, n: 1,
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
    const button = event.target.closest('button[data-task]');
    if (button) openTask(button.dataset.task);
  });
  $('work-toggle').addEventListener('click', () => {
    setWorkPanel($('work-panel').classList.contains('collapsed'));
  });
  $('work-close').addEventListener('click', () => setWorkPanel(false));
  $('sidebar-toggle').addEventListener('click', () => {
    document.body.classList.toggle('sidebar-open');
  });
  $('automation-btn').addEventListener('click', () => toast('自动化编排将在后续版本开放'));
  $('plugins-btn').addEventListener('click', () => toast('插件市场将在后续版本开放'));

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
  renderWorkTabs(selectedPlatform);
  renderTasks();

  window.AgentApp = { getSessionId: () => sessionId, getHotTopics: () => hotTopics, switchView };

  loadKnowledgeBase();
  loadImageStyles();
  loadXhsStatus();
  loadModels();
  populateSamplePages();
  input.focus();
})();
