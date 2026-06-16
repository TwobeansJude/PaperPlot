const state = {
  sessionId: null,
  fileInfo: null,
  generating: false,
};

const $ = (sel) => document.querySelector(sel);

function init() {
  loadSettings();
  bindEvents();
}

async function loadSettings() {
  try {
    const r = await fetch('/api/settings');
    const d = await r.json();
    if (d.success && d.settings) {
      $('#apiKey').value = d.settings.api_key || '';
      $('#apiUrl').value = d.settings.api_url || 'https://api.deepseek.com';
      $('#model').value = d.settings.model || 'deepseek-chat';
    }
  } catch (e) {
    console.log('Settings load skipped');
  }
}

function bindEvents() {
  $('#btnSettings').onclick = () => {
    const panel = $('#settingsPanel');
    panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
  };

  $('#btnCloseSettings').onclick = () => {
    $('#settingsPanel').style.display = 'none';
  };

  $('#btnSaveSettings').onclick = async () => {
    const settings = {
      api_key: $('#apiKey').value.trim(),
      api_url: $('#apiUrl').value.trim(),
      model: $('#model').value,
    };
    try {
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings),
      });
      const d = await r.json();
      if (d.success) {
        $('#settingsPanel').style.display = 'none';
        showToast('设置已保存');
      }
    } catch (e) {
      showToast('保存失败: ' + e.message);
    }
  };

  const zone = $('#uploadZone');
  const input = $('#fileInput');

  zone.onclick = () => input.click();
  zone.ondragover = (e) => { e.preventDefault(); zone.classList.add('dragover'); };
  zone.ondragleave = () => zone.classList.remove('dragover');
  zone.ondrop = (e) => {
    e.preventDefault();
    zone.classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    if (file) uploadFile(file);
  };

  input.onchange = () => {
    const file = input.files[0];
    if (file) uploadFile(file);
  };

  $('#btnRemoveFile').onclick = () => {
    state.sessionId = null;
    state.fileInfo = null;
    $('#uploadZone').style.display = '';
    $('#fileInfo').style.display = 'none';
    $('#fileInput').value = '';
    $('#messages').innerHTML = '';
  };

  $('#btnGenerate').onclick = doGenerate;
  $('#requirementInput').onkeydown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      doGenerate();
    }
  };
}

async function uploadFile(file) {
  showOverlay('正在上传文件...');
  const fd = new FormData();
  fd.append('file', file);

  try {
    const r = await fetch('/api/upload', { method: 'POST', body: fd });
    const d = await r.json();
    if (d.success) {
      state.sessionId = d.session_id;
      state.fileInfo = d.file_info;
      $('#fileName').textContent = file.name;
      $('#fileMeta').textContent =
        `${d.file_info.shape[0]}行 × ${d.file_info.shape[1]}列 | ${d.file_info.sheet_names.length}个Sheet`;
      $('#uploadZone').style.display = 'none';
      $('#fileInfo').style.display = 'flex';
      $('#messages').innerHTML = '';
      addSystemMessage(`已加载: ${file.name}`);
    } else {
      showToast('上传失败: ' + d.error);
    }
  } catch (e) {
    showToast('上传失败: ' + e.message);
  }
  hideOverlay();
}

async function doGenerate() {
  if (state.generating) return;
  const requirement = $('#requirementInput').value.trim();
  if (!requirement) {
    showToast('请输入需求描述');
    return;
  }
  if (!state.sessionId) {
    showToast('请先上传文件');
    return;
  }

  state.generating = true;
  $('#btnGenerate').disabled = true;
  $('#requirementInput').disabled = true;
  showOverlay('AI 正在分析和生成图表...');

  addUserMessage(requirement);
  $('#requirementInput').value = '';

  try {
    const r = await fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: state.sessionId,
        requirement: requirement,
      }),
    });
    const d = await r.json();

    if (d.success) {
      addAIMessage(d.analysis, d.image, d.code);
    } else {
      addErrorMessage(d.error, d.analysis, d.code);
    }
  } catch (e) {
    addErrorMessage('请求失败: ' + e.message);
  }

  state.generating = false;
  $('#btnGenerate').disabled = false;
  $('#requirementInput').disabled = false;
  $('#requirementInput').focus();
  hideOverlay();
}

function addUserMessage(text) {
  const div = document.createElement('div');
  div.className = 'message message-user';
  div.innerHTML = `<div class="bubble">${escapeHtml(text)}</div>`;
  appendMessage(div);
}

function addSystemMessage(text) {
  const div = document.createElement('div');
  div.className = 'message';
  div.innerHTML = `<div class="empty-state"><p>${escapeHtml(text)}</p></div>`;
  appendMessage(div);
}

function addAIMessage(analysis, imageBase64, code) {
  const div = document.createElement('div');
  div.className = 'message message-ai';
  let html = '';
  if (analysis) {
    html += `<div class="analysis">${escapeHtml(analysis)}</div>`;
  }
  if (imageBase64) {
    html += `
      <div class="chart-container">
        <img src="data:image/png;base64,${imageBase64}" alt="生成的图表">
        <div class="chart-actions">
          <button class="btn btn-primary" onclick="downloadImage(this)">下载图片</button>
          <button class="btn btn-secondary" onclick="copyCode(this)">复制代码</button>
        </div>
      </div>`;
  }
  if (code) {
    html += `<details class="code-block" open>
      <summary>生成代码</summary>
      <div class="code-editor-wrapper">
        <textarea class="code-editor" data-original="${escapeHtml(code)}">${escapeHtml(code)}</textarea>
        <div class="code-actions">
          <button class="btn btn-primary" onclick="submitEditedCode(this)">提交修改</button>
          <button class="btn btn-secondary" onclick="resetCode(this)">重置</button>
        </div>
      </div>
    </details>`;
  }
  div.innerHTML = html;
  appendMessage(div);
}

function addErrorMessage(error, analysis, code) {
  const div = document.createElement('div');
  div.className = 'message message-error';
  let html = `<div class="bubble">生成失败: ${escapeHtml(error)}</div>`;
  if (analysis) {
    html += `<div class="analysis">分析思路: ${escapeHtml(analysis)}</div>`;
  }
  if (code) {
    html += `<details style="margin-top:8px;font-size:12px;"><summary>查看生成代码</summary><pre style="background:#f9fafb;padding:8px;border-radius:4px;overflow-x:auto;font-size:11px;">${escapeHtml(code)}</pre></details>`;
  }
  div.innerHTML = html;
  appendMessage(div);
}

function appendMessage(el) {
  const msgs = $('#messages');
  msgs.appendChild(el);
  msgs.scrollTop = msgs.scrollHeight;
}

function downloadImage(btn) {
  const img = btn.closest('.chart-container').querySelector('img');
  const a = document.createElement('a');
  a.href = img.src;
  a.download = 'chart_' + Date.now() + '.png';
  a.click();
}

function copyCode(btn) {
  const textarea = btn.closest('.code-block').querySelector('.code-editor');
  if (textarea) {
    navigator.clipboard.writeText(textarea.value).then(() => {
      showToast('代码已复制到剪贴板');
    }).catch(() => {
      showToast('复制失败，请手动选中复制');
    });
  }
}

function submitEditedCode(btn) {
  const codeBlock = btn.closest('.code-block');
  const textarea = codeBlock.querySelector('.code-editor');
  const code = textarea.value.trim();

  if (!code) {
    showToast('代码不能为空');
    return;
  }

  btn.disabled = true;
  btn.textContent = '执行中...';
  showOverlay('正在执行修改后的代码...');

  fetch('/api/execute_code', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code: code }),
  })
  .then(r => r.json())
  .then(d => {
    if (d.success) {
      const chartContainer = codeBlock.previousElementSibling;
      if (chartContainer && chartContainer.classList.contains('chart-container')) {
        chartContainer.querySelector('img').src = 'data:image/png;base64,' + d.image;
      }
      showToast('生成成功！');
    } else {
      showToast('执行失败: ' + d.error);
    }
  })
  .catch(e => {
    showToast('请求失败: ' + e.message);
  })
  .finally(() => {
    btn.disabled = false;
    btn.textContent = '提交修改';
    hideOverlay();
  });
}

function resetCode(btn) {
  const codeBlock = btn.closest('.code-block');
  const textarea = codeBlock.querySelector('.code-editor');
  const originalCode = textarea.dataset.original;
  if (originalCode) {
    textarea.value = originalCode;
    showToast('已重置为原始代码');
  }
}

function showOverlay(text) {
  $('#overlayText').textContent = text;
  $('#overlay').style.display = 'flex';
}

function hideOverlay() {
  $('#overlay').style.display = 'none';
}

function showToast(msg) {
  let toast = $('#toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'toast';
    toast.style.cssText =
      'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1f2937;color:#fff;padding:10px 24px;border-radius:8px;font-size:14px;z-index:2000;transition:opacity 0.3s;';
    document.body.appendChild(toast);
  }
  toast.textContent = msg;
  toast.style.opacity = '1';
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { toast.style.opacity = '0'; }, 2500);
}

function escapeHtml(text) {
  const d = document.createElement('div');
  d.textContent = text;
  return d.innerHTML;
}

init();