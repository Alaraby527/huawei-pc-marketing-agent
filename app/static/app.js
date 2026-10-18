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
  let lastCoverKey = '';
  let lastHotKey = '';
  let loadingNode = null;
  let quickStartData = null;
  let quickSelection = {};
  let xhsPollTimer = null;
  let xhsTopicsActive = false;

  const PLATFORM_NAMES = { xiaohongshu: '小红书', wechat_article: '公众号', moments: '门店朋友圈', poster: '门店海报' };
  const ROLE_NAMES = { hook: '封面', scene: '场景', proof: '事实', detail: '细节', decision: '决策', close: '收尾' };
  const FIELD_NAMES = { audience: '客户画像', budget: '预算', scenario: '使用场景', preferences: '偏好', required_software: '必用软件 / 系统', objective: '内容目标' };

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

  function setBusy(busy) {
    sendBtn.disabled = busy;
    sendBtn.textContent = busy ? '生成中…' : '发送';
    if (busy) {
      loadingNode = addMessage('assistant', '<p class="loading">正在读取知识库、核验素材并组织内容，请稍候…</p>');
    } else if (loadingNode) {
      loadingNode.remove();
      loadingNode = null;
    }
  }

  async function requestTurn(payload) {
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
      setBusy(false);
    }
  }

  function send(message, slotUpdates) {
    const payload = { session_id: sessionId, message: message || '', engine: selectedEngine };
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
  }

  function renderChat(result) {
    if (result.message) {
      addMessage('assistant', '<p>' + escapeHtml(result.message) + '</p>');
    }
    if (result.status === 'candidates' && result.candidates && result.candidates.length && !result.draft) {
      const names = result.candidates.map((candidate, index) => (index + 1) + '. ' + candidate.model_name).join('；');
      addMessage('assistant', '<p>已找到候选：' + escapeHtml(names) + '</p>' +
        '<p class="hint">在右侧候选卡点击“选 N”继续；也可以直接回复“选1”。</p>');
    }
    if (result.issues && result.issues.length) {
      addMessage('assistant', '<ul class="issues">' +
        result.issues.slice(0, 4).map(item => '<li>' + escapeHtml(item) + '</li>').join('') + '</ul>');
    }
  }

  function renderOptions(questions) {
    const usable = (questions || []).filter(q => q.options && q.options.length &&
      q.field !== 'candidate_id' && q.field !== 'draft_decision');
    if (!usable.length) {
      optionsWrap.hidden = true;
      optionsWrap.innerHTML = '';
      $('hotspot-box').hidden = false;
      return;
    }
    optionsWrap.hidden = false;
    $('hotspot-box').hidden = true;
    optionsWrap.innerHTML = usable.map(question => {
      const options = question.options.map(option => {
        const slot = option.slot_updates ? ' data-slot="' + escapeHtml(JSON.stringify(option.slot_updates)) + '"' : '';
        return '<button type="button" class="chip" data-message="' + escapeHtml(option.message || option.label) + '"' + slot + '>' +
          escapeHtml(option.label) + '</button>';
      }).join('');
      return '<div class="option-group"><div class="option-label">' +
        escapeHtml(FIELD_NAMES[question.field] || question.question) + '</div><div class="chip-row">' + options + '</div></div>';
    }).join('');
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
    if (!draft || !draft.pages || !draft.pages.length) {
      grid.hidden = true;
      empty.hidden = false;
      return;
    }
    empty.hidden = true;
    grid.hidden = false;
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
    if (!draft) { badge.textContent = '等待生成'; return; }
    const meta = draft.engine_meta || {};
    if (meta.provider === 'rule-fallback' || (meta.provider || '').startsWith('rule-fallback-compliance')) badge.textContent = '规则回退';
    else if (meta.provider === 'local-rule-template') badge.textContent = '规则引擎';
    else if (meta.model) badge.textContent = '模型 · ' + meta.model;
    else badge.textContent = '规则引擎';
  }

  function renderHotTopics(topics, note) {
    $('hotspot-note').textContent = note || '';
    if (!topics || !topics.length) {
      $('hotspot-chips').innerHTML = '<span class="chip muted">暂无热点</span>';
      return;
    }
    $('hotspot-chips').innerHTML = topics.slice(0, 3).map(topic =>
      '<button type="button" class="chip" data-hotspot="' + escapeHtml(topic.title) + '"' +
      (topic.url ? ' title="' + escapeHtml(topic.url) + '"' : '') + '>' +
      escapeHtml(topic.title) + '</button>').join('');
  }

  async function loadHotspots(slots) {
    if (xhsTopicsActive) return;
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
        renderHotTopics([], data.message || '小红书暂不可用');
        return;
      }
      const topics = data.topics || [];
      if (!topics.length) {
        xhsTopicsActive = false;
        renderHotTopics([], '小红书未返回相关笔记');
        return;
      }
      xhsTopicsActive = true;
      $('xhs-login').textContent = '小红书已连接';
      renderHotTopics(topics, '小红书实时笔记');
    } catch (error) {
      xhsTopicsActive = false;
      renderHotTopics([], '小红书连接失败');
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
        if (Date.now() - started > 120000) {
          closeXhsModal();
          toast('二维码已过期，请重新连接');
        }
      } catch (error) {
        // Keep polling; a transient network error should not close the dialog.
      }
    }, 2000);
  }

  async function xhsLogin() {
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
      $('xhs-status').textContent = '请用小红书 App 扫码登录并完成授权';
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
    document.querySelectorAll('.tab').forEach(item => item.classList.toggle('active', item.dataset.tab === name));
    document.querySelectorAll('.tab-panel').forEach(panel => { panel.hidden = true; });
    const panel = $('panel-' + name);
    if (panel) panel.hidden = false;
  }

  async function loadQuickStart() {
    if (sessionId || lastResult) return;
    try {
      const response = await fetch('/api/v1/questions');
      if (!response.ok) return;
      const data = await response.json();
      quickStartData = data.core;
      renderQuickStart();
    } catch (error) {
      // The normal typed flow still works when this optional panel fails.
    }
  }

  function renderQuickStart() {
    if (sessionId || lastResult || !quickStartData) return;
    const labels = { audience: '客户画像', budget: '预算', scenario: '使用场景' };
    const groups = Object.entries(quickStartData).map(([field, question]) => {
      const options = (question.options || []).map(option =>
        '<button type="button" class="chip" data-quick-field="' + field + '" data-quick-message="' +
        escapeHtml(option.message) + '">' + escapeHtml(option.label) + '</button>').join('');
      return '<div class="option-group"><div class="option-label">' + (labels[field] || field) +
        '</div><div class="chip-row">' + options + '</div></div>';
    }).join('');
    optionsWrap.innerHTML = groups +
      '<div class="quick-start-footer"><span class="hint">三项都选好后开始；也可以直接在输入框描述</span>' +
      '<button type="button" class="btn-primary" id="quick-start-send" disabled>开始选品</button></div>';
    optionsWrap.hidden = false;
    $('hotspot-box').hidden = true;
  }

  function sendQuickStart() {
    const message = ['audience', 'budget', 'scenario'].map(field => quickSelection[field]).filter(Boolean).join('，');
    if (!message) return;
    addMessage('operator', '<p>' + escapeHtml(message) + '</p>');
    quickSelection = {};
    optionsWrap.hidden = true;
    send(message);
  }

  function updateQuickStartState() {
    const ready = ['audience', 'budget', 'scenario'].every(field => quickSelection[field]);
    const button = $('quick-start-send');
    if (button) button.disabled = !ready;
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
    send(text);
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
    const quick = event.target.closest('button[data-quick-field]');
    if (quick) {
      const field = quick.dataset.quickField;
      quickSelection[field] = quickSelection[field] === quick.dataset.quickMessage ? null : quick.dataset.quickMessage;
      optionsWrap.querySelectorAll('button[data-quick-field="' + field + '"]').forEach(button => {
        button.classList.toggle('active', button === quick && Boolean(quickSelection[field]));
      });
      updateQuickStartState();
      return;
    }
    if (event.target.closest('#quick-start-send')) {
      sendQuickStart();
      return;
    }
    const button = event.target.closest('button[data-message]');
    if (!button) return;
    addMessage('operator', '<p>' + escapeHtml(button.textContent) + '</p>');
    const slot = button.dataset.slot ? JSON.parse(button.dataset.slot) : null;
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

  $('hotspot-chips').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-hotspot]');
    if (!button) return;
    const text = '参考热点：' + button.dataset.hotspot;
    addMessage('operator', '<p>' + escapeHtml(text) + '</p>');
    send(text);
  });

  $('xhs-login').addEventListener('click', xhsLogin);
  $('xhs-close').addEventListener('click', closeXhsModal);
  $('xhs-modal').addEventListener('click', (event) => {
    if (event.target.id === 'xhs-modal') closeXhsModal();
  });

  platformSwitch.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-platform]');
    if (!button) return;
    selectedPlatform = button.dataset.platform;
    platformSwitch.querySelectorAll('.platform-btn').forEach(item => item.classList.toggle('active', item === button));
    send('', { platform: selectedPlatform });
  });

  $('engine-switch').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-engine]');
    if (!button) return;
    selectedEngine = button.dataset.engine;
    $('engine-switch').querySelectorAll('button').forEach(item => item.classList.toggle('active', item === button));
    if (!lastResult || !lastResult.draft) {
      $('engine-badge').textContent = selectedEngine === 'llm' ? '模型引擎' : '规则引擎';
    }
  });

  document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => switchTab(tab.dataset.tab));
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

  $('generate-cover').addEventListener('click', generateCover);

  loadKnowledgeBase();
  loadHotspots(null);
  loadQuickStart();
  loadXhsStatus();
  populateSamplePages();
  $('engine-badge').textContent = selectedEngine === 'llm' ? '模型引擎' : '规则引擎';
})();
