(() => {
  const log = document.getElementById('chat-log');
  const form = document.getElementById('composer');
  const input = document.getElementById('input');
  const sendBtn = document.getElementById('send');
  const quickActions = document.getElementById('quick-actions');
  const kbBadge = document.getElementById('kb-badge');
  const publishState = document.getElementById('publish-state');

  let sessionId = null;
  let lastRevision = null;

  function addMessage(role, html) {
    const div = document.createElement('div');
    div.className = 'msg ' + role;
    div.innerHTML = html;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text == null ? '' : String(text);
    return div.innerHTML;
  }

  function renderAssistant(result) {
    let html = '<p>' + escapeHtml(result.message || '(无文字说明)') + '</p>';
    if (result.candidates && result.candidates.length) {
      html += '<div class="candidate-list">' + result.candidates.map((c, i) => {
        const eligibility = c.eligibility === 'verified' ? '证据完整' : '待核实';
        const price = c.current_price
          ? '当前核验价 ¥' + escapeHtml(c.current_price.amount) + '｜' + escapeHtml(c.current_price.configuration)
          : '未取得当前有效价格';
        const unknowns = (c.unknowns || []).map(u => '<li>' + escapeHtml(u) + '</li>').join('');
        return '<div class="candidate">' +
          '<div class="candidate-head"><strong>' + (i + 1) + '. ' + escapeHtml(c.model_name) + '</strong>' +
          '<span>' + escapeHtml(eligibility) + '</span></div>' +
          '<div class="candidate-meta">预算：' + escapeHtml(c.budget_status || '未核验') + '｜' + price + '</div>' +
          (unknowns ? '<ul class="candidate-unknowns">' + unknowns + '</ul>' : '') +
          '</div>';
      }).join('') + '</div><p class="hint">回复“选1”确认产品；待核实项不会自动当成已满足。</p>';
    }
    if (result.questions && result.questions.length) {
      html += '<ul class="q-list">' + result.questions.map(q => '<li>' + escapeHtml(q.question) + '</li>').join('') + '</ul>';
    }
    if (result.issues && result.issues.length) {
      html += '<ul class="issues">' + result.issues.slice(0, 4).map(i => '<li>' + escapeHtml(i) + '</li>').join('') + '</ul>';
    }
    addMessage('assistant', html);
  }

  function renderDraft(result) {
    const empty = document.getElementById('draft-empty');
    const content = document.getElementById('draft-content');
    if (!result.draft) {
      empty.hidden = false;
      content.hidden = true;
      return;
    }
    empty.hidden = true;
    content.hidden = false;
    const d = result.draft;
    document.getElementById('draft-title').textContent = d.title || '';
    document.getElementById('draft-body').textContent = d.body || '';
    document.getElementById('draft-cta').textContent = d.cta ? ('行动引导：' + d.cta) : '';
    document.getElementById('draft-tags').innerHTML = (d.tags || []).map(t => '<span>#' + escapeHtml(t) + '</span>').join('');
    const imagesWrap = document.getElementById('draft-images');
    const images = (result.handoff && result.handoff.images) || [];
    imagesWrap.innerHTML = images.map(img => '<img src="' + img.access_url + '" alt="' + escapeHtml(img.caption) + '" title="' + escapeHtml(img.caption) + '">').join('');

    const exportLink = document.getElementById('export-link');
    if (result.can_publish && sessionId) {
      exportLink.href = '/api/v1/conversations/' + sessionId + '/export?format=markdown';
      exportLink.hidden = false;
    } else {
      exportLink.hidden = true;
    }
  }

  function renderEvidence(result) {
    const empty = document.getElementById('evidence-empty');
    const content = document.getElementById('evidence-content');
    const facts = (result.handoff && result.handoff.facts) || [];
    if (!facts.length) { empty.hidden = false; content.innerHTML = ''; return; }
    empty.hidden = true;
    content.innerHTML = facts.map(f => (
      '<div class="evidence-item"><div class="label">' + escapeHtml(f.text.split('\n')[0]) + '</div>' +
      '<div class="source">来源：' + escapeHtml(f.source.title) + '</div></div>'
    )).join('');
  }

  function renderVerify(result) {
    const empty = document.getElementById('verify-empty');
    const content = document.getElementById('verify-content');
    const report = result.compliance_report;
    if (!report) {
      empty.hidden = false; content.innerHTML = '';
      publishState.textContent = result.can_publish ? '已通过核验，可以复制或导出' : '尚未通过核验，不可发布';
      publishState.className = result.can_publish ? 'ready' : '';
      return;
    }
    empty.hidden = true;
    let html = '<div class="verify-item ' + (report.passed ? 'ok' : 'error') + '">合规核验：' + (report.passed ? '通过' : '未通过') + '</div>';
    html += (report.errors || []).map(e => '<div class="verify-item error">问题：' + escapeHtml(e) + '</div>').join('');
    html += (report.warnings || []).map(w => '<div class="verify-item warning">提示：' + escapeHtml(w) + '</div>').join('');
    content.innerHTML = html;

    publishState.textContent = result.can_publish ? '已通过核验，可以复制或导出' : '尚未通过核验，不可发布';
    publishState.className = result.can_publish ? 'ready' : '';
  }

  function render(result) {
    sessionId = result.session_id || sessionId;
    lastRevision = result.revision;
    renderAssistant(result);
    renderDraft(result);
    renderEvidence(result);
    renderVerify(result);
    quickActions.hidden = !(result.status === 'draft_ready' || result.draft);
  }

  async function send(message) {
    sendBtn.disabled = true;
    try {
      const response = await fetch('/api/v1/conversations/turn', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, message }),
      });
      const result = await response.json();
      if (!response.ok) {
        addMessage('assistant', '<p>' + escapeHtml(result.message || '请求失败，请重试') + '</p>');
        return;
      }
      render(result);
    } catch (error) {
      addMessage('assistant', '<p>网络错误，请检查连接后重试。</p>');
    } finally {
      sendBtn.disabled = false;
    }
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    addMessage('operator', '<p>' + escapeHtml(text) + '</p>');
    input.value = '';
    send(text);
  });

  quickActions.addEventListener('click', (event) => {
    const action = event.target.dataset && event.target.dataset.action;
    if (!action) return;
    addMessage('operator', '<p>' + escapeHtml(action) + '</p>');
    send(action);
  });

  document.getElementById('copy-draft').addEventListener('click', async () => {
    const title = document.getElementById('draft-title').textContent;
    const body = document.getElementById('draft-body').textContent;
    const tags = document.getElementById('draft-tags').textContent;
    try {
      await navigator.clipboard.writeText(title + '\n\n' + body + '\n\n' + tags);
      alert('已复制正文');
    } catch (error) {
      alert('复制失败，请手动选中文字复制');
    }
  });

  document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach(p => p.hidden = true);
      tab.classList.add('active');
      document.getElementById('panel-' + tab.dataset.tab).hidden = false;
    });
  });

  (async () => {
    try {
      const response = await fetch('/api/v1/knowledge/connection');
      const data = await response.json();
      if (response.ok) {
        kbBadge.textContent = '知识库：已连接 · ' + (data.knowledge_base_name || '');
        kbBadge.className = 'badge ok';
      } else {
        kbBadge.textContent = '知识库：暂不可用';
        kbBadge.className = 'badge err';
      }
    } catch (error) {
      kbBadge.textContent = '知识库：检测失败';
      kbBadge.className = 'badge err';
    }
  })();
})();
