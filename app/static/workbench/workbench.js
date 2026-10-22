/* 图文工作室: the user-driven half of the flow.
 *
 * Carries the conversation's fixed product and the current copy into the studio, lets the
 * user generate images from the KB reference photo, and lets them edit the copy - which is
 * saved through the agent's own fact check, not around it.
 */
const params = new URLSearchParams(location.search);
const conversationId = params.get('conversation') || localStorage.getItem('hwpc_conversation') || '';
const $ = (id) => document.getElementById(id);
const state = { content: null, revision: null, artifactId: '', productId: '', refImageId: '' };

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function say(node, text, kind) {
  node.className = 'result' + (kind ? ' ' + kind : '');
  node.textContent = text || '';
}

if (!conversationId) {
  say($('copy-result'), '没有会话上下文：请从对话页点“去图文工作室”进入，或在网址后加 ?conversation=<会话ID>。', 'err');
}

$('back-link').href = conversationId ? ('/?conversation=' + encodeURIComponent(conversationId)) : '/';

/* ---------------------------------------------------------------- materials */

async function loadMaterials() {
  if (!conversationId) return;
  const response = await fetch('/api/v2/conversations/' + encodeURIComponent(conversationId) + '/studio');
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    say($('copy-result'), '读取材料失败：' + (body.detail?.message || body.message || response.status), 'err');
    return;
  }
  const data = await response.json();
  state.productId = data.product?.product_id || '';
  $('product-tag').textContent = data.product?.model
    ? (data.product.model + (data.product.audience ? ' · ' + data.product.audience : ''))
    : '尚未确定产品';
  $('channel-hint').textContent = data.capability?.channel_configured
    ? ('生图通道已配置 · 参考图模型 ' + (data.capability.reference_model || ''))
    : '生图通道未配置（缺少 IMAGE_API_KEY）';
  $('notice').textContent = data.notice || '';

  const thumbs = $('kb-thumbs');
  thumbs.innerHTML = '';
  if (!(data.images || []).length) {
    thumbs.innerHTML = '<p class="muted">本次创作没有绑定的知识库原图。先在对话里确认产品，再从对话进入工作室。</p>';
  }
  (data.images || []).forEach((image, index) => {
    const figure = document.createElement('figure');
    figure.className = 'thumb' + (index === 0 ? ' sel' : '');
    figure.innerHTML = '<img src="' + esc(image.access_url) + '" alt="' + esc(image.caption) + '"/>'
      + '<figcaption>' + esc(image.caption || image.image_id) + '</figcaption>';
    figure.onclick = () => {
      document.querySelectorAll('.thumb').forEach((node) => node.classList.remove('sel'));
      figure.classList.add('sel');
      state.refImageId = image.image_id;
    };
    thumbs.appendChild(figure);
    if (index === 0) state.refImageId = image.image_id;
  });

  renderCopy(data.copy);
}

/* ---------------------------------------------------------------- copy */

function renderCopy(copy) {
  const host = $('copy-area');
  if (!copy) {
    $('copy-meta').textContent = '（暂无）';
    host.innerHTML = '<p class="muted">本次创作还没有文案。可以先在对话里生成，或在下方直接写。</p>';
    state.content = { title: '', pages: [] };
    state.revision = null;
    state.artifactId = '';
    bindNewCopy();
    return;
  }
  state.content = JSON.parse(JSON.stringify(copy.content || {}));
  state.revision = copy.revision;
  state.artifactId = copy.artifact_id;
  const check = copy.check || {};
  $('copy-meta').textContent = '第 ' + copy.revision + ' 版'
    + (check.passed === false ? ' · 上次检查未通过' : ' · 已通过检查');
  host.innerHTML = '';

  const titleLabel = document.createElement('label');
  titleLabel.className = 'field';
  titleLabel.textContent = '标题';
  const titleInput = document.createElement('input');
  titleInput.id = 'copy-title';
  titleInput.value = state.content.title || '';
  titleLabel.appendChild(titleInput);
  host.appendChild(titleLabel);

  (state.content.pages || []).forEach((page, index) => {
    const block = document.createElement('div');
    block.className = 'page-block';
    block.innerHTML = '<div class="page-head">第 ' + (index + 1) + ' 页'
      + (page.role ? ' · ' + esc(page.role) : '') + '</div>';
    const pageTitle = document.createElement('input');
    pageTitle.value = page.title || '';
    pageTitle.placeholder = '页面标题';
    pageTitle.dataset.field = 'title';
    pageTitle.dataset.index = index;
    const body = document.createElement('textarea');
    body.value = page.text || '';
    body.rows = 4;
    body.placeholder = '页面正文';
    body.dataset.field = 'text';
    body.dataset.index = index;
    block.appendChild(pageTitle);
    block.appendChild(body);
    host.appendChild(block);
  });
  say($('copy-result'), '');
}

function collectCopy() {
  const content = JSON.parse(JSON.stringify(state.content || {}));
  const title = $('copy-title');
  if (title) content.title = title.value;
  document.querySelectorAll('#copy-area [data-field]').forEach((node) => {
    const index = Number(node.dataset.index);
    if (content.pages && content.pages[index]) content.pages[index][node.dataset.field] = node.value;
  });
  return content;
}

function bindNewCopy() {
  const host = $('copy-area');
  host.innerHTML = '<label class="field">标题<input id="copy-title" placeholder="标题"/></label>'
    + '<label class="field">正文<textarea id="copy-body" rows="10" placeholder="直接写正文；保存时会做事实检查"></textarea></label>';
}

$('save-copy').onclick = async () => {
  if (!conversationId) return;
  const button = $('save-copy');
  button.disabled = true;
  say($('copy-result'), '保存中…');
  try {
    let content = collectCopy();
    const plain = $('copy-body');
    if (plain) content = { title: ($('copy-title') || {}).value || '', text: plain.value };
    const payload = {
      content,
      artifact_id: state.artifactId || '',
      base_revision: state.revision,
      product_id: $('bind-product').checked ? state.productId : '',
      note: '工作室改文案',
    };
    const response = await fetch('/api/v2/conversations/' + encodeURIComponent(conversationId) + '/studio/copy', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      const check = data.check || {};
      const detail = (check.errors || []).slice(0, 6).join('\n');
      say($('copy-result'), '未保存：' + (data.message || data.error || response.status)
        + (detail ? '\n' + detail : ''), 'err');
      return;
    }
    state.revision = data.revision;
    state.artifactId = data.artifact_id;
    const warnings = (data.check?.warnings || []).slice(0, 4).join('；');
    say($('copy-result'), '已保存为第 ' + data.revision + ' 版，事实检查通过。'
      + (warnings ? '\n提示：' + warnings : ''), 'ok');
    $('copy-meta').textContent = '第 ' + data.revision + ' 版 · 已通过检查';
  } finally {
    button.disabled = false;
  }
};

/* ---------------------------------------------------------------- generate */

$('generate').onclick = async () => {
  if (!conversationId) return;
  const button = $('generate');
  button.disabled = true;
  $('gen-thumbs').innerHTML = '';
  say($('gen-result'), '生成中，通常需要 25–100 秒…');
  $('gen-status').textContent = '';
  try {
    const response = await fetch('/api/v2/conversations/' + encodeURIComponent(conversationId) + '/studio/image', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        prompt: $('prompt').value, ratio: $('ratio').value, size: $('size').value,
        image_id: state.refImageId || '',
      }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      say($('gen-result'), '未生成：' + (data.message || data.error || response.status), 'err');
      return;
    }
    say($('gen-result'), data.notice || '已生成。', 'warn');
    $('gen-status').textContent = '通道 ' + (data.channel || '') + ' · 参考图 '
      + (data.reference?.caption || data.reference?.image_id || '');
    (data.images || []).forEach((image) => {
      const figure = document.createElement('figure');
      figure.className = 'thumb';
      figure.innerHTML = '<img src="' + esc(image.access_url) + '" alt="生成场景图"/>'
        + '<figcaption>生成场景图</figcaption>';
      $('gen-thumbs').appendChild(figure);
    });
  } catch (error) {
    say($('gen-result'), '请求失败：' + error, 'err');
  } finally {
    button.disabled = false;
  }
};

loadMaterials().catch((error) => say($('copy-result'), '加载异常：' + error, 'err'));