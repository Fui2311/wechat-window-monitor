/**
 * WeChat Lecture Tracker - Native Frontend Control Application
 * Pure vanilla ES6, zero third-party dependencies, offline-first.
 */

// Application State
const state = {
  currentTab: 'tab-events',
  currentFilter: 'all',
  searchQuery: '',
  selectedDb: localStorage.getItem('selectedDatabase') || '',
  databases: [],
  services: [],
  events: [],
  messages: [],
  messageFilter: 'all',
  messageSearch: '',
  messageOffset: 0,
  messageLimit: 100,
  messageRequest: 0,
  eventsRequest: 0,
  outboxRequest: 0,
  previewRequest: 0,
  statusRequest: 0,
  databaseRequest: 0,
  servicesRequest: 0,
  logsRequest: 0,
  outbox: [],
  service: {},
  config: {},
  refreshTimer: null,
  refreshInterval: 5000,
  autoScrollLogs: true,
  currentModalEvent: null,
  isBackendOnline: true,
  isConsoleStopping: false,
  isConsoleStopped: false,
  consoleStoppedAt: null,
};

// Utilities
function formatTime(isoString) {
  if (!isoString) return '';
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return '';
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  } catch (e) {
    return '';
  }
}

function displayEventTime(isoString, rawText) {
  const formatted = formatTime(isoString);
  if (formatted) return formatted;
  if (rawText && String(rawText).trim()) return String(rawText).trim();
  return '未注明';
}

function signupLinkHtml(value) {
  if (!value) return '未提供';
  try {
    const url = new URL(String(value));
    if (!['http:', 'https:'].includes(url.protocol)) return '未提供';
    return `<a href="${escapeHtml(url.href)}" target="_blank" rel="noopener noreferrer">打开报名链接</a>`;
  } catch (error) {
    return '未提供';
  }
}

function formatClockTime(isoString) {
  if (!isoString) return '--';
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return String(isoString);
    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  } catch (e) {
    return '--';
  }
}

function escapeHtml(text) {
  if (text == null) return '';
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function reportDataError(section, error) {
  if (state.isConsoleStopping || state.isConsoleStopped) return;
  const errors = state.lastDataErrors || (state.lastDataErrors = {});
  const message = String(error.message || error).slice(0, 180);
  if (errors[section] !== message) showToast(`${section}读取失败：${message}`, 'error', 6000);
  errors[section] = message;
}

function showToast(message, type = 'info', duration = 3500) {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;

  let iconSvg = '';
  if (type === 'success') {
    iconSvg = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color: var(--green);"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>`;
  } else if (type === 'error') {
    iconSvg = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color: var(--red);"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>`;
  } else {
    iconSvg = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color: var(--blue);"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>`;
  }

  toast.innerHTML = `${iconSvg}<span>${escapeHtml(message)}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(10px)';
    setTimeout(() => toast.remove(), 250);
  }, duration);
}

const modalOpeners = new Map();

function openModal(id, focusId) {
  const modal = document.getElementById(id);
  if (!modal.classList.contains('active')) modalOpeners.set(id, document.activeElement);
  modal.classList.add('active');
  modal.tabIndex = -1;
  const target = focusId ? document.getElementById(focusId) : modal.querySelector('button:not(:disabled)');
  (target || modal).focus();
}

function closeModal(modal, restoreFocus = true) {
  if (!modal.classList.contains('active')) return;
  modal.classList.remove('active');
  const opener = modalOpeners.get(modal.id);
  modalOpeners.delete(modal.id);
  if (restoreFocus && opener?.isConnected) opener.focus();
}

function showConfirm(title, message, onConfirm) {
  const modal = document.getElementById('confirmModal');
  document.getElementById('confirmModalTitle').textContent = title;
  document.getElementById('confirmModalMessage').textContent = message;

  const okBtn = document.getElementById('confirmModalOkBtn');
  const cancelBtn = document.getElementById('confirmModalCancelBtn');

  const cleanup = () => {
    closeModal(modal);
    okBtn.onclick = null;
    cancelBtn.onclick = null;
  };

  okBtn.onclick = () => {
    cleanup();
    onConfirm();
  };
  cancelBtn.onclick = cleanup;
  openModal('confirmModal', 'confirmModalCancelBtn');
}

// API Calls
async function apiGet(endpoint) {
  const res = await fetch(endpoint);
  if (!res.ok) {
    const text = await res.text();
    let errMsg = text || `HTTP ${res.status}`;
    try {
      const errJson = JSON.parse(text);
      if (errJson && errJson.error) errMsg = errJson.error;
    } catch (e) {}
    throw new Error(errMsg);
  }
  return await res.json();
}

async function apiPost(endpoint, body = {}) {
  const res = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    let errMsg = text || `HTTP ${res.status}`;
    try {
      const errJson = JSON.parse(text);
      if (errJson && errJson.error) errMsg = errJson.error;
    } catch (e) {}
    throw new Error(errMsg);
  }
  return await res.json();
}

// Status & Dashboard updater
async function refreshStatus() {
  if (state.isConsoleStopping || state.isConsoleStopped) return;
  const request = ++state.statusRequest;
  const statusBadge = document.getElementById('serviceStatusBadge');
  const statusText = document.getElementById('serviceStatusText');
  const dot = statusBadge.querySelector('.status-indicator');
  const offlineBanner = document.getElementById('offlineBanner');

  try {
    const data = await apiGet('/api/status');
    if (request !== state.statusRequest) return;
    if (state.isConsoleStopping || state.isConsoleStopped) return;
    state.isBackendOnline = true;
    if (offlineBanner) offlineBanner.style.display = 'none';

    const svc = data.service || {};
    const cfg = data.config || {};
    state.service = svc;
    state.config = cfg;
    renderProcessing(cfg.processing || {});

    const groupName = cfg.group || '未配置群名';
    document.getElementById('targetGroupName').textContent = groupName;

    // Fill switch group input if not focused or dirty
    const inputSwitch = document.getElementById('inputSwitchGroup');
    if (inputSwitch && document.activeElement !== inputSwitch && !inputSwitch.value.trim()) {
      inputSwitch.value = cfg.group || '';
    }

    if (svc.running) {
      if (svc.status !== 'monitoring') {
        statusBadge.className = 'badge badge-warning';
        dot.className = 'status-indicator pulse-amber';
        statusText.textContent = svc.status === 'waiting_for_weixin' ? '等待微信窗口' : '服务启动中';
        document.getElementById('kpiServiceState').textContent = svc.status === 'waiting_for_weixin' ? '等待微信' : '启动中';
        document.getElementById('kpiServiceState').style.color = 'var(--amber)';
      } else {
        statusBadge.className = 'badge badge-success';
        dot.className = 'status-indicator pulse-green';
        statusText.textContent = '监控中';
        document.getElementById('kpiServiceState').textContent = '监控运行中';
        document.getElementById('kpiServiceState').style.color = 'var(--green)';
      }
    } else {
      statusBadge.className = 'badge badge-danger';
      dot.className = 'status-indicator pulse-red';
      statusText.textContent = '服务未启动';
      document.getElementById('kpiServiceState').textContent = '已停止';
      document.getElementById('kpiServiceState').style.color = 'var(--text-secondary)';
    }

    // KPI 1: Service
    const pidText = svc.running && svc.pid ? `PID: ${svc.pid}` : '无运行进程';
    const hbText = svc.heartbeat_at ? `心跳: ${formatClockTime(svc.heartbeat_at)}` : '--';
    document.getElementById('kpiServiceDetail').textContent = `${pidText} · ${hbText} · ${svc.send_enabled ? '真实发信' : '仅预览'}`;

    // KPI 2: Capture
    document.getElementById('kpiObservedCount').textContent = svc.observed_messages || 0;
    document.getElementById('kpiCaptureDetail').textContent = `采样: ${svc.snapshots || 0} · 视口断档: ${svc.capture_gaps || 0} 次`;

    // KPI 4: Outbox
    const dueCount = svc.due_previews || 0;
    const sentCount = svc.email_sent || 0;
    if (!state.deliveryLoaded) document.getElementById('kpiOutboxDetail').textContent = `当前管理服务：待投递 ${dueCount} · 已发 ${sentCount}`;

    // Config tab values
    document.getElementById('cfgGroup').textContent = cfg.group || '--';
    document.getElementById('cfgTimezone').textContent = cfg.timezone || 'Asia/Shanghai';
    document.getElementById('cfgReminders').textContent = (cfg.reminder_minutes && cfg.reminder_minutes.length > 0)
      ? `${cfg.reminder_minutes.join(', ')} 分钟`
      : '已关闭 (符合校区规则的讲座消息转发原文)';
    document.getElementById('cfgMaxAttempts').textContent = `${cfg.max_attempts || 5} 次`;
    document.getElementById('cfgCaptureSource').textContent = cfg.capture_source || 'macos_ax';

    const emailCfg = cfg.email || {};
    document.getElementById('cfgSmtpHost').textContent = emailCfg.host || 'smtp.qq.com';
    document.getElementById('cfgSmtpPort').textContent = `${emailCfg.port || 465} (${emailCfg.security || 'SSL'})`;
    document.getElementById('cfgCredentials').textContent = data.email_configured ? '本机已保存邮箱配置（发信时校验）' : '尚未配置邮箱';
    state.emailStatus = data.email_status || {};
    const macKeychain = state.emailStatus.backend === 'macos_keychain';
    document.getElementById('btnAuthorizeEmail').textContent = !data.email_configured ? '首次配置指南' : macKeychain ? '授权邮箱' : '检查邮箱';
    const sharedEmail = state.emailStatus.settings_shared ? `凭据来自「${state.emailStatus.source_group}」。` : '';
    const environmentOnly = state.emailStatus.backend === 'environment';
    document.getElementById('emailSetupHelp').textContent = sharedEmail + (data.email_configured
      ? macKeychain ? '后台不会主动弹密码；授权失效时点击授权邮箱并在系统弹窗完成授权。此操作不发测试邮件。'
        : '当前使用本机加密凭据或环境变量；点击检查邮箱验证可读取状态，不发测试邮件。'
      : environmentOnly ? '当前平台使用环境变量提供 SMTP 凭据。请在启动控制台和监控服务的同一终端设置下列变量，随后重启控制台。'
        : '首次使用请在终端运行下方命令，按提示隐藏输入 SMTP 授权码。配置命令会实际发送一封测试邮件；完成后刷新此页。');
    const configArgument = state.emailStatus.backend === 'windows_dpapi'
      ? `'${(state.emailStatus.setup_config_path || 'config.local.toml').replace(/'/g, "''")}'`
      : `'${(state.emailStatus.setup_config_path || 'config.local.toml').replace(/'/g, "'\\''")}'`;
    document.getElementById('emailSetupCommand').textContent = data.email_configured ? ''
      : environmentOnly ? (state.emailStatus.environment_variables || []).join('、')
        : `${state.emailStatus.backend === 'windows_dpapi' ? 'PowerShell: ' : ''}wechat-track --config ${configArgument} email setup`;
    document.getElementById('cfgSendState').textContent = !svc.running ? '监控已停止' :
      (svc.send_enabled ? (svc.send_ready ? '真实发信已就绪' : '发信模式：等待本机凭据') : '仅预览模式');

    document.getElementById('cfgDataDir').textContent = cfg.data_dir || '--';
    document.getElementById('cfgJournal').textContent = svc.journal_mode || 'DELETE';
    document.getElementById('cfgBackupTime').textContent = svc.backup_at ? formatTime(svc.backup_at) : '尚未备份';
    updateDbTypeBadge();

  } catch (err) {
    if (request !== state.statusRequest) return;
    if (state.isConsoleStopping || state.isConsoleStopped) return;
    state.isBackendOnline = false;
    console.warn('Failed to refresh status:', err);

    // Show offline banner and reset badges to offline state immediately
    if (offlineBanner) {
      offlineBanner.style.display = 'flex';
      const textEl = document.getElementById('offlineBannerText');
      if (textEl) textEl.textContent = `后台服务连接失败 (${err.message})。请检查控制台服务是否正在运行。`;
    }

    statusBadge.className = 'badge badge-danger';
    dot.className = 'status-indicator pulse-red';
    statusText.textContent = '连接断开 / 离线';

    const kpiState = document.getElementById('kpiServiceState');
    if (kpiState) {
      kpiState.textContent = '后台离线';
      kpiState.style.color = 'var(--red)';
    }

    const kpiDetail = document.getElementById('kpiServiceDetail');
    if (kpiDetail) kpiDetail.textContent = '无法连接到控制后台';
  }
}

// Update database indicator badge
function updateDbTypeBadge() {
  const badge = document.getElementById('dbTypeBadge');
  if (!badge) return;

  const currentDb = state.databases.find(d => d.path === state.selectedDb);
  const scope = document.getElementById('serviceOperationScope');
  if (scope) scope.textContent = `当前查看：${currentDb?.label || '活动库'}。监控启停、处理设置、邮箱及到期任务预览均作用于管理群「${state.config.group || '当前群'}」。`;
  const returnButton = document.getElementById('btnReturnActive');
  if (returnButton) returnButton.hidden = !currentDb || currentDb.is_active;
  if (!currentDb) {
    badge.className = 'badge badge-success';
    badge.textContent = '当前活动库';
    return;
  }

  if (currentDb.is_active) {
    badge.className = 'badge badge-success';
    badge.textContent = '当前群活动库';
  } else if (currentDb.is_demo) {
    badge.className = 'badge badge-info';
    badge.textContent = 'Demo 演示库 (只读)';
  } else if (currentDb.is_live_service) {
    badge.className = 'badge badge-info';
    badge.textContent = `运行中服务 [${currentDb.group_name}]（只读查看）`;
  } else {
    badge.className = 'badge badge-warning';
    badge.textContent = currentDb.group_name ? `历史库 [${currentDb.group_name}] (只读)` : '历史只读库 (非当前群)';
  }
}

// Fetch database list
function selectDatabase(path) {
  if (state.selectedDb !== path) {
    state.eventsRequest++;
    state.outboxRequest++;
    state.messageRequest++;
    state.previewRequest++;
    state.events = [];
    state.outbox = [];
    state.messages = [];
    state.messageOffset = 0;
    state.currentModalEvent = null;
    for (const id of ['emailPreviewModal', 'eventDetailModal']) closeModal(document.getElementById(id), false);
    for (const id of ['kpiEventsCount', 'tabBadgeEvents', 'countFilterAll', 'kpiOutboxCount', 'tabBadgeOutbox']) {
      document.getElementById(id).textContent = '--';
    }
    document.getElementById('mailDeliveryStatus').textContent = '正在读取所选数据源…';
    document.getElementById('kpiOutboxDetail').textContent = '正在读取所选数据源…';
    document.getElementById('eventsListContainer').textContent = '正在读取所选数据源…';
    document.getElementById('outboxTableBody').textContent = '';
    state._lastEventsRenderKey = null;
    state._lastOutboxRenderKey = null;
  }
  state.selectedDb = path;
  localStorage.setItem('selectedDatabase', path);
  const select = document.getElementById('databaseSelect');
  if (select) select.value = path;
  updateDbTypeBadge();
}

async function loadDatabases() {
  if (state.isConsoleStopping || state.isConsoleStopped) return;
  const request = ++state.databaseRequest;
  try {
    const data = await apiGet('/api/databases');
    if (request !== state.databaseRequest || state.isConsoleStopping || state.isConsoleStopped) return;
    state.databases = data.databases || [];
    const select = document.getElementById('databaseSelect');
    select.innerHTML = '';

    const activeDb = state.databases.find(d => d.is_active);
    const activePath = activeDb?.path || '';
    const previousActive = localStorage.getItem('selectedDatabaseActive');
    if (previousActive !== activePath) selectDatabase(activePath);
    localStorage.setItem('selectedDatabaseActive', activePath);

    // If state.selectedDb is empty or no longer in databases, default to active database
    if (!state.selectedDb || !state.databases.some(d => d.path === state.selectedDb)) {
      selectDatabase(activePath || state.databases[0]?.path || '');
    }

    state.databases.forEach((db) => {
      const opt = document.createElement('option');
      opt.value = db.path;
      opt.textContent = `${db.label} (${db.event_count} 讲座)`;
      if (state.selectedDb === db.path) {
        opt.selected = true;
      }
      select.appendChild(opt);
    });

    updateDbTypeBadge();

    select.onchange = (e) => {
      if (state.isConsoleStopped || state.isConsoleStopping) return;
      selectDatabase(e.target.value);
      loadEvents();
      state.messageOffset = 0;
      loadMessages();
      loadOutbox();
    };
  } catch (err) {
    if (request !== state.databaseRequest || state.isConsoleStopping || state.isConsoleStopped) return;
    console.error('Failed to load databases:', err);
    reportDataError('数据源', err);
  }
}

// Load Events
async function loadEvents() {
  if (state.isConsoleStopping || state.isConsoleStopped) return;
  const request = ++state.eventsRequest;
  const db = state.selectedDb;
  try {
    const url = db ? `/api/events?db=${encodeURIComponent(db)}` : '/api/events';
    const data = await apiGet(url);
    if (request !== state.eventsRequest || db !== state.selectedDb || state.isConsoleStopping || state.isConsoleStopped) return;
    state.events = data.events || [];
    const selectedDatabase = state.databases.find(db => db.path === state.selectedDb);
    if (selectedDatabase) {
      selectedDatabase.event_count = state.events.length;
      const option = Array.from(document.getElementById('databaseSelect').options)
        .find(item => item.value === state.selectedDb);
      if (option) option.textContent = `${selectedDatabase.label} (${state.events.length} 讲座)`;
    }

    // Every lecture-related message counts, regardless of optional time fields.
    document.getElementById('kpiEventsCount').textContent = state.events.length;
    document.getElementById('kpiEventsDetail').textContent = '石牌校区或未注明校区的讲座可通知';
    document.getElementById('tabBadgeEvents').textContent = state.events.length;
    document.getElementById('countFilterAll').textContent = state.events.length;

    renderEvents();
  } catch (err) {
    if (request !== state.eventsRequest || db !== state.selectedDb || state.isConsoleStopping || state.isConsoleStopped) return;
    document.getElementById('kpiEventsDetail').textContent = '读取失败；请刷新重试。当前内容仅为所选数据源上次成功读取的记录。';
    if (!state.events.length) document.getElementById('eventsListContainer').textContent = '所选数据源读取失败，请刷新重试。';
    console.error('Failed to load events:', err);
    reportDataError('讲座数据', err);
  }
}

function formatCaptureTime(value) {
  const formatted = formatTime(value);
  return formatted ? `${formatted}:${String(new Date(value).getSeconds()).padStart(2, '0')}` : '未记录';
}

function nestedMessageText(message, depth = 0) {
  if (depth > 6) return '';
  const parts = [];
  for (const attachment of message.attachments || []) {
    parts.push(`[附件] ${attachment.name || attachment.kind || '未注明类型'}${attachment.text ? '\n' + attachment.text : ''}`);
  }
  for (const child of message.children || []) {
    parts.push(`[转发子消息] ${child.sender || '发送者未注明'} · ${formatCaptureTime(child.sent_at)}\n${child.text || '无可读正文'}`);
    const nested = nestedMessageText(child, depth + 1);
    if (nested) parts.push(nested);
  }
  return parts.join('\n\n');
}

// Message reads never run capture, reanalysis or notification dispatch.
async function loadMessages() {
  if (state.isConsoleStopping || state.isConsoleStopped) return;
  const request = ++state.messageRequest;
  const db = state.selectedDb;
  const status = document.getElementById('messagesLoadStatus');
  if (state.messagesDb !== db) {
    state.messagesDb = db;
    state.messages = [];
    state._lastMessagesRenderKey = null;
    document.getElementById('messagesListContainer').textContent = '';
    document.getElementById('messagesLastCapture').textContent = '--';
    document.getElementById('messagesLatestReceived').textContent = '--';
    document.getElementById('messagesFreshnessHint').textContent = '正在读取所选数据库...';
    document.getElementById('messagesPageInfo').textContent = '';
    for (const id of ['tabBadgeMessages', 'countMessagesAll', 'countMessagesNonLecture', 'countMessagesLecture']) {
      document.getElementById(id).textContent = '--';
    }
  }
  document.getElementById('btnMessagesPrevious').disabled = true;
  document.getElementById('btnMessagesNext').disabled = true;
  status.textContent = '正在读取采集记录...';
  try {
    const params = new URLSearchParams({ category: state.messageFilter, q: state.messageSearch,
      limit: state.messageLimit, offset: state.messageOffset });
    if (db) params.set('db', db);
    const data = await apiGet(`/api/messages?${params}`);
    if (request !== state.messageRequest || db !== state.selectedDb || state.isConsoleStopping || state.isConsoleStopped) return;
    if (state.messageOffset > 0 && state.messageOffset >= data.total) {
      state.messageOffset = 0;
      return loadMessages();
    }
    state.messages = data.messages || [];
    const counts = data.counts || {};
    document.getElementById('tabBadgeMessages').textContent = counts.all || 0;
    document.getElementById('countMessagesAll').textContent = counts.all || 0;
    document.getElementById('countMessagesNonLecture').textContent = counts.non_lecture || 0;
    document.getElementById('countMessagesLecture').textContent = counts.lecture || 0;
    document.getElementById('messagesLastCapture').textContent = formatCaptureTime(data.last_success_at);
    document.getElementById('messagesLatestReceived').textContent = formatCaptureTime(data.latest_received_at);
    const selected = state.databases.find(item => item.path === db);
    const age = (Date.now() - new Date(data.last_success_at).getTime()) / 1000;
    document.getElementById('messagesFreshnessHint').textContent = !selected?.is_active
      ? '历史 / 演示数据库，只读展示已保存记录。'
      : !data.last_success_at ? '尚无成功采样记录，请检查监控服务和微信窗口。'
      : age > 90 ? '最近成功采样距今已超过 90 秒，请检查监控状态和微信窗口。'
      : '最近有成功采样；没有新消息入库时，消息列表不会增加。';
    status.textContent = `读取成功：${formatCaptureTime(new Date().toISOString())}${data.unreadable_count ? ` · ${data.unreadable_count} 条存储记录无法解析` : ''}`;
    const first = data.total ? state.messageOffset + 1 : 0;
    document.getElementById('messagesPageInfo').textContent = `${first}–${state.messageOffset + state.messages.length} / ${data.total} 条`;
    document.getElementById('btnMessagesPrevious').disabled = state.messageOffset === 0;
    document.getElementById('btnMessagesNext').disabled = !data.has_more;
    if (state.lastDataErrors) delete state.lastDataErrors['采集消息'];
    renderMessages();
  } catch (error) {
    if (request !== state.messageRequest || db !== state.selectedDb || state.isConsoleStopping || state.isConsoleStopped) return;
    status.textContent = `读取失败：${error.message}。当前内容为上次成功读取的记录，请刷新重试。`;
    reportDataError('采集消息', error);
  }
}

function renderMessages() {
  const key = JSON.stringify([state.selectedDb, state.messageFilter, state.messageSearch, state.messageOffset, state.messages]);
  if (key === state._lastMessagesRenderKey) return;
  state._lastMessagesRenderKey = key;
  const container = document.getElementById('messagesListContainer');
  container.innerHTML = '';
  if (!state.messages.length) {
    container.innerHTML = '<div class="empty-state"><h4>暂无符合条件的采集消息</h4><p>可切换分类、清除搜索，或检查最近成功采样时间。首次可见基线可能只记录为已见。</p></div>';
    return;
  }
  const kinds = {text: '文本', forward: '转发记录', card: '卡片', image: '图片', file: '文件', audio: '语音', video: '视频', system: '系统消息'};
  for (const message of state.messages) {
    const card = document.createElement('article');
    card.className = 'event-card';
    const text = message.text || '无可读正文';
    const nested = nestedMessageText(message);
    card.innerHTML = `
      <div class="event-header">
        <strong>${escapeHtml(message.sender || '发送者未注明')}</strong>
        <div class="filter-left"><span class="badge ${message.category === 'lecture' ? 'badge-info' : 'badge-neutral'}">${message.category === 'lecture' ? '讲座相关' : '非讲座 · 未关联讲座记录'}</span><span class="badge badge-neutral">${escapeHtml(kinds[message.kind] || message.kind || '未注明类型')}</span></div>
      </div>
      <div class="capture-note">消息时间：${escapeHtml(formatCaptureTime(message.sent_at))} · 采集入库：${escapeHtml(formatCaptureTime(message.received_at))}<br>来源群：${escapeHtml(message.group || '未注明')}</div>
      <div class="message-text">${escapeHtml(text.length > 700 ? text.slice(0, 700) + '…' : text)}</div>
      ${text.length > 700 ? `<details class="message-details"><summary>展开完整原文（${text.length} 字符）</summary><div class="message-text">${escapeHtml(text)}</div></details>` : ''}
      ${nested ? `<details class="message-details"><summary>查看已读取的转发子消息 / 附件文字</summary><div class="message-text">${escapeHtml(nested)}</div></details>` : ''}
      ${message.kind === 'forward' && !(message.children || []).length ? '<div class="capture-note">转发正文未读到，此处仅显示已采集的卡片摘要。</div>' : ''}`;
    container.appendChild(card);
  }
}

// Render Events
function renderEvents() {
  const container = document.getElementById('eventsListContainer');

  let filtered = state.events;

  // Search Filter
  if (state.searchQuery.trim()) {
    const q = state.searchQuery.toLowerCase();
    filtered = filtered.filter(e =>
      (e.title && e.title.toLowerCase().includes(q)) ||
      (e.summary && e.summary.toLowerCase().includes(q)) ||
      (e.source_text && e.source_text.toLowerCase().includes(q))
    );
  }

  const renderKey = `${state.selectedDb}|${state.searchQuery}|${filtered.length}|` +
    filtered.map(e => `${e.id}:${e.version || 1}:${e.updated_at || ''}:${JSON.stringify(e.delivery || {})}`).join(';');
  if (state._lastEventsRenderKey === renderKey) {
    return;
  }
  state._lastEventsRenderKey = renderKey;

  container.innerHTML = '';

  if (filtered.length === 0) {
    const currentGroup = (state.databases.find(db => db.path === state.selectedDb) || {}).group_name || (state.config && state.config.group) || '目标微信群';
    container.innerHTML = `
      <div class="empty-state">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>
          <line x1="16" y1="2" x2="16" y2="6"></line>
          <line x1="8" y1="2" x2="8" y2="6"></line>
          <line x1="3" y1="10" x2="21" y2="10"></line>
        </svg>
        <h4>未找到讲座相关消息</h4>
        <p>当前所选数据库中暂无符合条件的讲座记录。当微信目标群【${escapeHtml(currentGroup)}】捕获到新的讲座通知时，将通知并展示在此处。</p>
      </div>
    `;
    return;
  }

  filtered.forEach((event) => {
    const card = document.createElement('div');
    card.className = 'event-card';

    const method = { rules: '本地规则', llm: 'LLM 提取', rules_fallback: 'LLM 失败 · 规则回退' }[event.processing_method];
    const statusBadge = '<span class="badge badge-info">讲座相关消息</span>' +
      (method ? `<span class="badge badge-info">${escapeHtml(method)}</span>` : '');
    const sourceGroup = event.source_group || (state.config && state.config.group) || '未注明';

    card.innerHTML = `
      <div class="event-header">
        <div class="event-title-area">
          <div class="event-badges">
            ${statusBadge}
          </div>
          <h3 class="event-title">${escapeHtml(event.title)}</h3>
        </div>
      </div>

      <div class="event-meta-grid">
        <div class="meta-item">
          <span class="meta-label">⏰ 讲座活动时间</span>
          <span class="meta-value">${escapeHtml(displayEventTime(event.start_at, event.activity_time_text))}</span>
        </div>
        <div class="meta-item">
          <span class="meta-label">⏳ 报名开始时间</span>
          <span class="meta-value">${escapeHtml(displayEventTime(event.signup_start_at, event.signup_start_text))}</span>
        </div>
        <div class="meta-item">
          <span class="meta-label">📢 来源群</span>
          <span class="meta-value">${escapeHtml(sourceGroup)}</span>
        </div>
      </div>

      <div class="event-meta-grid">
        <div class="meta-item">
          <span class="meta-label">🔗 报名链接</span>
          <span class="meta-value">${signupLinkHtml(event.signup_url)}</span>
        </div>
      </div>
      <div class="event-summary">
        ${escapeHtml(event.summary || event.source_text || '暂无正文')}
      </div>
      ${deliveryHtml(event.delivery)}

      <div class="event-footer">
        <span>更新时间：${escapeHtml(formatTime(event.updated_at) || '未注明')}</span>
        <div class="event-actions">
          <button class="btn btn-secondary btn-sm btn-view-detail" data-id="${event.id}">查看原文</button>
          <button class="btn btn-primary btn-sm btn-preview-email" data-id="${event.id}">邮件正文预览</button>
        </div>
      </div>
    `;

    // Hook buttons
    card.querySelector('.btn-view-detail').onclick = () => openEventDetail(event);
    card.querySelector('.btn-preview-email').disabled = state.isConsoleStopped || state.isConsoleStopping;
    card.querySelector('.btn-preview-email').onclick = () => openEmailPreview(event.id);

    container.appendChild(card);
  });
}

function deliveryHtml(delivery) {
  if (!delivery) return '';
  const good = delivery.state === 'sent';
  const detail = delivery.message || '';
  const retry = delivery.retry_at ? `<div>下次重试：${escapeHtml(formatTime(delivery.retry_at))}</div>` : '';
  const error = delivery.error ? `<div>最近错误：${escapeHtml(delivery.error)}</div>` : '';
  return `<div class="delivery-panel ${good ? 'delivery-success' : 'delivery-attention'}"><strong>邮件：${escapeHtml(delivery.label || delivery.state)}</strong><div>${escapeHtml(detail)}</div>${retry}${error}${delivery.attempts ? `<div>失败尝试：${delivery.attempts} 次</div>` : ''}</div>`;
}

// Load Outbox
async function loadOutbox() {
  if (state.isConsoleStopping || state.isConsoleStopped) return;
  const request = ++state.outboxRequest;
  const db = state.selectedDb;
  try {
    const url = db ? `/api/outbox?db=${encodeURIComponent(db)}` : '/api/outbox';
    const data = await apiGet(url);
    if (request !== state.outboxRequest || db !== state.selectedDb || state.isConsoleStopping || state.isConsoleStopped) return;
    state.outbox = data.jobs || [];
    state.deliveryLoaded = true;
    const states = state.outbox.map(job => job.delivery?.state);
    const sent = states.filter(s => s === 'sent').length;
    const waiting = states.filter(s => !['sent','superseded','expired','coalesced','simulation'].includes(s)).length;
    document.getElementById('kpiOutboxDetail').textContent = `所选数据源：已提交 ${sent} · 未发送 ${waiting}`;
    const problem = state.outbox.find(job => ['blocked_credentials','failed','blocked_stopped','retry_wait','queued_late','preview_only'].includes(job.delivery?.state));
    const service = data.delivery_service || {};
    const banner = document.getElementById('mailDeliveryStatus');
    banner.classList.toggle('delivery-attention', Boolean(problem));
    banner.textContent = service.simulation ? 'Demo 演示数据：仅供预览，所有演示任务均不会真实发送。' : problem ? `${service.group || '所选群'}：${problem.delivery.label}。${problem.delivery.message}${problem.delivery.retry_at ? ' 下次重试：' + formatTime(problem.delivery.retry_at) : ''}`
      : `${service.group || '所选数据源'}：${sent} 封已提交邮件服务器，${waiting} 封等待处理。`;

    document.getElementById('kpiOutboxCount').textContent = state.outbox.length;
    document.getElementById('tabBadgeOutbox').textContent = state.outbox.length;

    renderOutbox();
  } catch (err) {
    if (request !== state.outboxRequest || db !== state.selectedDb || state.isConsoleStopping || state.isConsoleStopped) return;
    document.getElementById('mailDeliveryStatus').textContent = '通知队列读取失败，请刷新重试；当前内容仅为所选数据源上次成功读取的记录。';
    console.error('Failed to load outbox:', err);
    reportDataError('通知队列', err);
  }
}

// Render Outbox
function renderOutbox() {
  const renderKey = `${state.selectedDb}|${state.outbox.length}|` +
    state.outbox.map(j => `${j.id}:${j.status}:${j.attempts}:${j.due_at}:${j.last_error || ''}:${JSON.stringify(j.delivery || {})}`).join(';');
  if (state._lastOutboxRenderKey === renderKey) {
    return;
  }
  state._lastOutboxRenderKey = renderKey;

  const tbody = document.getElementById('outboxTableBody');
  tbody.innerHTML = '';

  if (state.outbox.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="9" style="text-align: center; padding: 36px; color: var(--text-tertiary);">
          暂无通知邮件排程任务
        </td>
      </tr>
    `;
    return;
  }

  const reasonLabels = {
    new: { label: '新增通知', class: 'badge-info' },
    reminder: { label: '到时提醒', class: 'badge-warning' },
    update: { label: '信息变更', class: 'badge-warning' },
    cancelled: { label: '讲座取消', class: 'badge-danger' },
    review: { label: '人工核实', class: 'badge-neutral' },
  };

  const statusLabels = {
    pending: { label: '待投递', class: 'badge-warning' },
    claimed: { label: '投递中', class: 'badge-info' },
    sending: { label: '发送中', class: 'badge-info' },
    sent: { label: '已投递', class: 'badge-success' },
    superseded: { label: '已作废 (新版本)', class: 'badge-neutral' },
    failed: { label: '投递失败', class: 'badge-danger' },
    dead: { label: '死信队列', class: 'badge-danger' },
  };

  state.outbox.forEach((job) => {
    const reasonInfo = reasonLabels[job.reason] || { label: job.reason, class: 'badge-neutral' };
    const statusInfo = statusLabels[job.status] || { label: job.status, class: 'badge-neutral' };
    const payload = job.payload || {};

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><strong>#${job.id}</strong></td>
      <td><span class="badge ${reasonInfo.class}">${reasonInfo.label}</span></td>
      <td style="max-width: 220px; font-weight: 500;">${escapeHtml(payload.title || '--')}</td>
      <td>v${job.version}</td>
      <td>${formatTime(job.due_at)}</td>
      <td>${job.delivery ? deliveryHtml(job.delivery) : `<span class="badge ${statusInfo.class}">${statusInfo.label}</span>`}</td>
      <td>${job.attempts || 0}</td>
      <td style="color: var(--red); max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
        ${escapeHtml(job.last_error || '--')}
      </td>
      <td>
        <button class="btn btn-secondary btn-sm btn-preview-job-email" data-id="${job.id}">预览邮件</button>
      </td>
    `;

    tr.querySelector('.btn-preview-job-email').disabled = state.isConsoleStopped || state.isConsoleStopping;
    tr.querySelector('.btn-preview-job-email').onclick = () => openEmailPreview(null, job.id);
    tbody.appendChild(tr);
  });
}

// Load Services (Multi-instance detection)
async function loadServices() {
  if (state.isConsoleStopping || state.isConsoleStopped) return;
  const request = ++state.servicesRequest;
  const tbody = document.getElementById('servicesTableBody');
  if (!tbody) return;

  try {
    const data = await apiGet('/api/services');
    if (request !== state.servicesRequest || state.isConsoleStopping || state.isConsoleStopped) return;
    state.services = data.services || [];
    const followers = state.services.filter(svc => svc.running && svc.processing_settings_shared).map(svc => svc.group);
    const scope = document.getElementById('processingScope');
    if (scope && !state.processing?.settings_shared) scope.textContent = `设置持续用于当前管理群【${state.config.group || '当前群'}】${followers.length ? '以及跟随此配置的服务【' + followers.join('、') + '】' : ''}。切换数据源仅查看对应群记录，不会更换监控群。`;


    const servicesKey = state.services.map(s => `${s.data_dir}:${s.instance}:${s.running}:${s.status}:${s.send_enabled}:${s.reset_supported}:${s.reset_at}:${s.processing_mode}:${s.processing_settings_shared}:${s.processing_last_at}:${s.processing_last_error}`).join(';');
    if (state._lastServicesRenderKey === servicesKey) {
      return;
    }
    state._lastServicesRenderKey = servicesKey;

    if (state.services.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-tertiary); padding: 16px;">未发现已启动的其他监控后端</td></tr>';
      return;
    }

    tbody.innerHTML = '';
    state.services.forEach((svc) => {
      const tr = document.createElement('tr');
      const isCur = svc.is_current;
      const running = svc.running;

      let statusBadge = '<span class="badge badge-neutral">已停止</span>';
      if (running) {
        statusBadge = svc.status === 'waiting_for_weixin'
          ? '<span class="badge badge-warning">等待微信</span>'
          : '<span class="badge badge-success">监控中</span>';
      }

      let opHtml = '<span style="color: var(--text-tertiary); font-size: 12px;">--</span>';
      if (running) {
        if (isCur) {
          opHtml = '<span class="badge badge-info" style="font-size: 11px;">当前管理中</span>';
        } else {
          opHtml = `<button class="btn btn-danger btn-xs btn-stop-other" data-dir="${escapeHtml(svc.data_dir)}">停止此后端</button>`;
        }
      }
      if (running && svc.group === '邮件测试' && svc.reset_supported) {
        opHtml += ' <button class="btn btn-danger btn-xs btn-reset-test">清空测试数据</button>';
      }
      if (svc.group === '邮件测试' && svc.reset_at) {
        opHtml += `<div style="font-size: 11px; color: var(--text-secondary); margin-top: 6px;">上次清空：${escapeHtml(formatTime(svc.reset_at))}</div>`;
      }
      opHtml += ' <button class="btn btn-secondary btn-xs btn-view-service-messages">查看消息</button>';
      const processingLabel = svc.processing_mode === 'llm' ? 'LLM 提取' :
        (svc.processing_mode === 'rules' ? '本地规则' : '旧版进程：未上报处理方式');
      const scopeLabel = svc.processing_settings_shared ? '跟随控制台处理设置' : '本服务独立设置';
      const lastProcessing = {llm:'LLM 成功', rules:'本地规则', rules_fallback:'LLM 失败，规则回退'}[svc.processing_last_method] || '尚无新消息处理';

      tr.innerHTML = `
        <td><strong>${escapeHtml(svc.group || '未命名')}</strong> ${isCur ? '<span class="badge badge-success" style="font-size: 10px;">当前</span>' : ''}</td>
        <td style="font-size: 11px; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(svc.data_dir)}">${escapeHtml(svc.data_dir)}</td>
        <td>${svc.pid || '--'}</td>
        <td>${statusBadge}<div style="font-size:11px;margin-top:6px">${escapeHtml(processingLabel)} · ${escapeHtml(scopeLabel)}<br>${escapeHtml(lastProcessing)}${svc.processing_last_error ? '<br>' + escapeHtml(svc.processing_last_error) : ''}</div></td>
        <td>${svc.send_enabled ? '<span class="badge badge-warning">真实发信</span>' : '仅预览'}</td>
        <td>${opHtml}</td>
      `;

      const btnStop = tr.querySelector('.btn-stop-other');
      tr.querySelector('.btn-view-service-messages').onclick = async () => {
        await loadDatabases();
        const db = state.databases.find(item => item.path === svc.data_dir.replace(/\/$/, '') + '/tracker.sqlite3');
        if (!db) return showToast('此服务尚无可查看的消息库', 'info');
        const select = document.getElementById('databaseSelect');
        select.value = db.path;
        select.dispatchEvent(new Event('change'));
        document.querySelector('[data-tab="tab-events"]').click();
      };
      const btnReset = tr.querySelector('.btn-reset-test');
      if (btnReset) {
        btnReset.onclick = () => showConfirm('清空邮件测试数据',
          `将删除“邮件测试”的采集消息、讲座记录、邮件队列和去重/卡片缓存，不备份。当前已加载的聊天消息会作为基线，不会补发。之后重新转发的讲座会再次通知。保持${svc.send_enabled ? '真实发信' : '仅预览'}模式，其他群数据保留。`, async () => {
            btnReset.disabled = true;
            showToast('正在清空测试数据并建立基线，请稍候…', 'info');
            try {
              const result = await apiPost('/api/services/reset', {
                data_dir: svc.data_dir, instance: svc.instance, confirm_group: '邮件测试'
              });
              showToast(`测试数据已清空，已建立 ${result.baseline_count} 条旧消息基线；可以重新转发讲座了`, 'success');
              state._lastServicesRenderKey = null;
              await loadServices();
              await refreshStatus();
              await Promise.all([loadEvents(), loadMessages(), loadOutbox()]);
            } catch (err) {
              showToast(`清空失败: ${err.message}`, 'error');
            } finally {
              btnReset.disabled = false;
            }
          });
      }
      if (btnStop) {
        btnStop.onclick = () => {
          showConfirm('停止其他监控服务', `确定要停止数据目录为 ${svc.data_dir} 的后端监控服务吗？系统将通过安全控制套接字平稳停止。`, async () => {
            try {
              showToast('正在停止服务...', 'info');
              const res = await apiPost('/api/services/stop', { data_dir: svc.data_dir });
              if (res.ok) {
                showToast('已成功停止该后端服务', 'success');
                loadServices();
                refreshStatus();
              }
            } catch (err) {
              showToast(`停止失败: ${err.message}`, 'error');
            }
          });
        };
      }

      tbody.appendChild(tr);
    });
  } catch (err) {
    if (request !== state.servicesRequest || state.isConsoleStopping || state.isConsoleStopped) return;
    console.warn('Failed to load services:', err);
    tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--red); padding: 16px;">获取服务列表失败: ${escapeHtml(err.message)}</td></tr>`;
  }
}

// Load Logs
async function loadLogs() {
  if (state.isConsoleStopping || state.isConsoleStopped) return;
  const request = ++state.logsRequest;
  const managedDir = state.config.data_dir;
  try {
    const data = await apiGet('/api/logs?lines=200');
    if (request !== state.logsRequest || managedDir !== state.config.data_dir || state.isConsoleStopping || state.isConsoleStopped) return;
    const container = document.getElementById('terminalBody');
    const lines = data.lines || [];

    const logsKey = lines.join('\n');
    if (state._lastLogsText === logsKey) {
      return;
    }
    state._lastLogsText = logsKey;

    if (lines.length === 0) {
      container.innerHTML = '<div class="log-line" style="color: #71717a;">暂无日志输出</div>';
      return;
    }

    container.innerHTML = lines.map((line) => {
      let className = 'log-line';
      if (line.includes('ERROR') || line.includes('error')) className += ' log-error';
      else if (line.includes('WARNING') || line.includes('warn')) className += ' log-warning';
      else if (line.includes('INFO')) className += ' log-info';
      return `<div class="${className}">${escapeHtml(line)}</div>`;
    }).join('');

    if (state.autoScrollLogs) {
      container.scrollTop = container.scrollHeight;
    }
  } catch (err) {
    if (request !== state.logsRequest || managedDir !== state.config.data_dir || state.isConsoleStopping || state.isConsoleStopped) return;
    console.error('Failed to load logs:', err);
    reportDataError('运行日志', err);
  }
}

// Modals Handling
function openEventDetail(event) {
  state.currentModalEvent = event;
  document.getElementById('modalEventTitle').textContent = event.title || '讲座详情';
  document.getElementById('modalEventTime').textContent = displayEventTime(event.start_at, event.activity_time_text);
  document.getElementById('modalSignupStart').textContent = displayEventTime(event.signup_start_at, event.signup_start_text);
  document.getElementById('modalSignupLink').innerHTML = signupLinkHtml(event.signup_url);
  document.getElementById('modalEventStatus').textContent = '讲座相关消息';
  document.getElementById('modalEventSummary').textContent = event.summary || '无摘要';
  document.getElementById('modalEventSourceText').textContent = event.source_text || '无原始正文';

  openModal('eventDetailModal');
}

async function openEmailPreview(eventId, jobId) {
  if (state.isConsoleStopped || state.isConsoleStopping) {
    showToast('控制台正在关闭或已关闭，无法拉取邮件预览', 'info');
    return;
  }
  const db = state.selectedDb;
  const request = ++state.previewRequest;
  try {
    let url = '/api/preview_email?';
    if (jobId) url += `job_id=${encodeURIComponent(jobId)}`;
    else url += `event_id=${encodeURIComponent(eventId)}`;

    if (state.selectedDb) url += `&db=${encodeURIComponent(state.selectedDb)}`;

    const data = await apiGet(url);
    if (request !== state.previewRequest || db !== state.selectedDb || state.isConsoleStopped || state.isConsoleStopping) return;
    document.getElementById('emailPreviewSubject').textContent = data.subject || '--';
    document.getElementById('emailPreviewFormat').textContent = {
      llm_summary: 'LLM 提取信息 + 完整原文', source: '原文通知', legacy: '历史通知格式',
    }[data.mail_format] || '历史通知格式';
    const previewBody = document.getElementById('emailPreviewBody');
    previewBody.classList.toggle('email-body-rendered', Boolean(data.html_body));
    if (data.html_body) {
      const frame = document.createElement('iframe');
      frame.title = '邮件正文预览';
      frame.setAttribute('sandbox', '');
      frame.referrerPolicy = 'no-referrer';
      frame.style.cssText = 'display:block;width:100%;height:560px;border:0;background:#f4f6f8';
      frame.srcdoc = data.html_body;
      previewBody.replaceChildren(frame);
    } else {
      previewBody.textContent = data.body || '--';
    }
    const selectedSource = state.databases.find(item => item.path === db);
    document.getElementById('emailPreviewFrom').textContent = selectedSource?.is_active
      ? (state.config.email?.from && state.config.email.from !== '未配置' ? state.config.email.from : '发信时按本机邮箱配置确定')
      : '只读内容预览；所属服务发信时确定发件人';

    openModal('emailPreviewModal');
  } catch (err) {
    if (request !== state.previewRequest || db !== state.selectedDb || state.isConsoleStopped || state.isConsoleStopping) return;
    showToast(`无法获取邮件预览: ${err.message}`, 'error');
  }
}

// Probe Action
async function runProbe() {
  if (state.isConsoleStopped || state.isConsoleStopping) {
    showToast('控制台正在关闭或已关闭，无法执行窗口探针', 'info');
    return;
  }
  const badge = document.getElementById('probeStatusBadge');
  const output = document.getElementById('probeJsonOutput');

  badge.className = 'badge badge-warning';
  badge.textContent = '探针检测中...';
  output.textContent = '正在检测微信窗口及无障碍 Accessibility 状态，请稍候...';

  try {
    const data = await apiGet('/api/probe');
    const probe = data.probe || {};

    if (probe.ok) {
      badge.className = 'badge badge-success';
      badge.textContent = '检测通过 (就绪)';
      showToast('微信独立群窗口已就绪并可读', 'success');
    } else {
      badge.className = 'badge badge-danger';
      badge.textContent = `失败: ${probe.status || '未就绪'}`;
      showToast(`探针检测未通过: ${probe.status || '请检查窗口'}`, 'error');
    }

    output.textContent = JSON.stringify(probe, null, 2);
  } catch (err) {
    badge.className = 'badge badge-danger';
    badge.textContent = '检测出错';
    output.textContent = `错误: ${err.message}`;
    showToast(`探针请求失败: ${err.message}`, 'error');
  }
}

// Service Controls
function setServiceButtonsDisabled(disabled) {
  if (!disabled && state.isConsoleStopped) {
    return;
  }
  ['btnStartService', 'btnStartSendService', 'btnStopService', 'btnRestartService'].forEach((id) => {
    const btn = document.getElementById(id);
    if (btn) btn.disabled = disabled;
  });
}

async function confirmSendService(action) {
  if (state.isConsoleStopped || state.isConsoleStopping) return;
  try {
    const backlog = await apiGet('/api/service/backlog');
    if (state.isConsoleStopped || state.isConsoleStopping) return;
    const details = `操作对象：管理群「${backlog.group}」。\n当前有 ${backlog.pending} 条待发送记录，其中 ${backlog.due} 条已到期、${backlog.retrying} 条有失败重试记录。最早计划时间：${formatTime(backlog.oldest_due_at) || '无'}。另有 ${backlog.failed} 条停止重试的失败记录，不会自动重发。\n\n开启后会真实连接 SMTP，投递符合当前规则的已有积压和后续新通知；过往通知也可能立即发送。这里显示的是当前快照，确认前仍可能新增任务。确定继续吗？`;
    showConfirm('确认管理群与已有积压', details, () => controlService(action, true, backlog.group));
  } catch (error) {
    showToast(`无法核对管理群待发送记录，未开启发信：${error.message}`, 'error');
  }
}

async function controlService(action, send = false, expectedGroup) {
  if (state.isConsoleStopped || state.isConsoleStopping) {
    showToast('控制台正在关闭或已关闭，无法执行监控操作', 'warning');
    return;
  }
  let endpoint = `/api/service/${action}`;
  if (action === 'start' && send) {
    endpoint = '/api/service/start_send';
  }

  setServiceButtonsDisabled(true);
  const actionLabel = action === 'start' ? (send ? '启动发信服务' : '启动预览监控') : (action === 'stop' ? '停止微信监控' : '重启监控');
  showToast(`正在${actionLabel}...`, 'info');

  try {
    await apiPost(endpoint, expectedGroup ? { expected_group: expectedGroup, send } : {});
    await refreshStatus();
    await loadServices();
    if (!state.isBackendOnline) throw new Error('无法确认操作后的后台状态，请恢复连接后检查');

    const isRunning = Boolean(state.service && state.service.running);
    if (action === 'start') {
      if (isRunning) {
        if (state.service.status === 'waiting_for_weixin') {
          showToast('监控已启动，当前正在等待微信窗口打开', 'info');
        } else {
          showToast(`监控已启动 (${state.service.send_enabled ? '真实发信模式' : '仅预览模式'})`, 'success');
        }
      } else {
        showToast(`监控启动未成功: ${state.service.last_error || '服务未处于运行状态'}`, 'error');
      }
    } else if (action === 'stop') {
      if (!isRunning) {
        showToast('微信监控已成功安全停止', 'success');
      } else {
        showToast('监控停止指令已发送，正在等待微信监控退出', 'info');
      }
    } else if (action === 'restart') {
      if (isRunning) {
        showToast('监控已重启成功并运行中', 'success');
      } else {
        showToast('监控已发送重启指令', 'info');
      }
    }
  } catch (err) {
    showToast(`操作失败: ${err.message}`, 'error');
    try { await refreshStatus(); } catch (e) {}
    try { await loadServices(); } catch (e) {}
  } finally {
    if (!state.isConsoleStopped) {
      setServiceButtonsDisabled(false);
    }
  }
}

// Console Shutdown & Snapshot UI
function applyConsoleStoppedUI() {
  state.isBackendOnline = false;
  const statusBadge = document.getElementById('serviceStatusBadge');
  if (statusBadge) {
    statusBadge.className = 'badge badge-neutral';
    const dot = statusBadge.querySelector('.status-indicator');
    if (dot) dot.className = 'status-indicator pulse-gray';
    const statusText = document.getElementById('serviceStatusText');
    if (statusText) statusText.textContent = '控制台已关闭';
  }

  const offlineBanner = document.getElementById('offlineBanner');
  if (offlineBanner) offlineBanner.style.display = 'none';
  const stoppedBanner = document.getElementById('consoleStoppedBanner');
  if (stoppedBanner) stoppedBanner.style.display = 'flex';

  const kpiState = document.getElementById('kpiServiceState');
  if (kpiState) {
    const lastState = kpiState.textContent === '--' ? '状态未知' : kpiState.textContent;
    kpiState.textContent = `${lastState}（快照）`;
    kpiState.style.color = 'var(--text-secondary)';
  }
  const kpiDetail = document.getElementById('kpiServiceDetail');
  if (kpiDetail) {
    kpiDetail.textContent = '微信监控保持原状态 · 页面展示关闭前快照';
  }

  const dbBadge = document.getElementById('dbTypeBadge');
  if (dbBadge && !dbBadge.textContent.includes('快照')) {
    dbBadge.textContent += ' · 快照';
  }

  const btnClose = document.getElementById('btnCloseConsole');
  if (btnClose) {
    btnClose.disabled = true;
    btnClose.textContent = '控制台已关闭';
    btnClose.className = 'btn btn-sm btn-secondary';
  }

  const autoSel = document.getElementById('autoRefreshSelect');
  if (autoSel) {
    autoSel.value = '0';
    autoSel.disabled = true;
  }
  const manualBtn = document.getElementById('manualRefreshBtn');
  if (manualBtn) {
    manualBtn.disabled = true;
    manualBtn.title = '控制台正在关闭或已关闭，无法刷新';
  }

  const disableIds = [
    'btnStartService',
    'btnStartSendService',
    'btnStopService',
    'btnRestartService',
    'btnProbe',
    'btnRunProbeAction',
    'btnTick',
    'btnRunDemo',
    'btnExportIcs',
    'btnHeaderSwitchGroup',
    'btnProbeGroupCandidate',
    'btnExecuteGroupSwitch',
    'btnRefreshServices',
    'btnRefreshLogs',
    'btnClearLogs',
    'inputSwitchGroup',
    'databaseSelect',
    'modalPreviewEmailBtn',
  ];
  disableIds.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = true;
  });

  document.querySelectorAll('.btn-stop-other, .btn-preview-email, .btn-preview-job-email').forEach((btn) => {
    btn.disabled = true;
  });
}

async function stopConsole() {
  showConfirm(
    '关闭网页控制台',
    '确定要关闭控制台后台服务吗？\n\n• 仅关闭网页后台：微信群监控服务将保持当前状态不变\n• 连接断开：所有打开此控制台的浏览器页面都会断开连接\n• 重新打开：可运行快捷启动的「微信讲座控制台」（wxconsole），再刷新本页。',
    async () => {
      state.isConsoleStopping = true;

      // Stop auto-refresh timer immediately
      if (state.refreshTimer) {
        clearInterval(state.refreshTimer);
        state.refreshTimer = null;
      }

      const btnClose = document.getElementById('btnCloseConsole');
      if (btnClose) {
        btnClose.disabled = true;
        btnClose.textContent = '正在关闭...';
      }
      showToast('正在向控制台后台发送关闭指令...', 'info');

      try {
        const res = await apiPost('/api/console/stop', {});
        if (res && res.ok) {
          state.isConsoleStopped = true;
          state.isConsoleStopping = false;
          state.consoleStoppedAt = new Date().toISOString();
          applyConsoleStoppedUI();
          showToast('网页控制台后台已关闭，微信监控继续保持原状态', 'info', 5000);
        } else {
          throw new Error((res && res.error) || '关闭请求未成功');
        }
      } catch (err) {
        state.isConsoleStopping = false;
        state.isConsoleStopped = false;
        if (btnClose) {
          btnClose.disabled = false;
          btnClose.innerHTML = `
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M18.36 6.64a9 9 0 1 1-12.73 0"></path>
              <line x1="12" y1="2" x2="12" y2="12"></line>
            </svg>
            关闭控制台
          `;
        }
        if (state.refreshInterval > 0) {
          startRefreshTimer();
        }
        showToast(`关闭控制台失败: ${err.message}`, 'error', 4500);
      }
    }
  );
}

// Periodic Refresh Scheduling (visibility-aware)
function runPeriodicRefresh() {
  if (state.isConsoleStopped || state.isConsoleStopping || document.hidden) return;
  refreshStatus();
  if (state.currentTab === 'tab-events') { loadEvents(); loadOutbox(); }
  else if (state.currentTab === 'tab-messages') loadMessages();
  else if (state.currentTab === 'tab-outbox') loadOutbox();
  else if (state.currentTab === 'tab-logs') loadLogs();
  else if (state.currentTab === 'tab-config') loadServices();
}

function startRefreshTimer() {
  if (state.refreshTimer) {
    clearInterval(state.refreshTimer);
    state.refreshTimer = null;
  }
  if (!state.isConsoleStopped && !state.isConsoleStopping && state.refreshInterval > 0) {
    state.refreshTimer = setInterval(runPeriodicRefresh, state.refreshInterval);
  }
}

function renderProcessing(cfg, refill = false) {
  state.processing = cfg;
  document.getElementById('llmTestHint').textContent = cfg.settings_shared
    ? '使用当前共享配置，仅发送下方输入的原文；不保存设置、不入库、不发信。缺少发送时间时不猜测相对日期。'
    : '仅发送下方输入的原文，可使用尚未保存的配置；不入库、不发信。缺少发送时间时不猜测相对日期。';
  document.getElementById('processingScope').textContent = cfg.settings_shared
    ? `当前使用共享处理配置（只读）：${cfg.settings_source}。请在此配置的控制台修改，修改将影响所有引用它的服务；下方可测试当前共享配置。`
    : '配置持续保存，适用于当前配置后续所有新消息。切换数据源仅查看记录，不改变处理配置。';
  processingBusy(Boolean(state.processingSaving));
  const mode = cfg.processing_mode || 'rules';
  const mailFormat = mode === 'llm' ? 'LLM 提取信息 + 完整原文（失败时回退原文）' : '原文通知；文件仅标注名称';
  document.getElementById('processingMailFormat').textContent = `当前新通知邮件格式：${mailFormat}。随处理方式一起切换，已入队通知保留生成时的格式。`;
  document.getElementById('cfgMailFormat').textContent = mailFormat;
  document.getElementById('processingCurrent').textContent = mode === 'llm' ? 'LLM 提取' : '本地规则';
  for (const [id, value] of [['btnProcessingRules', 'rules'], ['btnProcessingLlm', 'llm']]) {
    const button = document.getElementById(id);
    button.setAttribute('aria-pressed', String(mode === value));
    button.classList.toggle('btn-primary', mode === value);
    button.classList.toggle('btn-secondary', mode !== value);
  }
  document.getElementById('llmKeyStatus').textContent = cfg.api_key_configured
    ? '密钥已持久保存；输入框清空仅为防止回显。无需重复填写，留空会保留已有密钥。' : '尚未配置密钥（本地接口可留空）';
  if (!state.processingInitialized || refill || !state.processingDirty) {
    document.getElementById('llmBaseUrl').value = cfg.llm_base_url || 'https://api.openai.com/v1';
    document.getElementById('llmModel').value = cfg.llm_model || '';
    document.getElementById('llmTimeout').value = cfg.llm_timeout_seconds || 15;
    document.getElementById('llmKeyEnv').value = cfg.llm_api_key_env || 'WECHAT_LLM_API_KEY';
    document.getElementById('llmDisableThinking').checked = cfg.llm_disable_thinking === true;
    state.processingInitialized = true;
  }
}

function processingPayload(mode) {
  return {
    processing_mode: mode || (state.processing || {}).processing_mode || 'rules',
    llm_base_url: document.getElementById('llmBaseUrl').value.trim(),
    llm_model: document.getElementById('llmModel').value.trim(),
    llm_api_key_env: document.getElementById('llmKeyEnv').value.trim(),
    llm_timeout_seconds: Number(document.getElementById('llmTimeout').value),
    llm_disable_thinking: document.getElementById('llmDisableThinking').checked,
    api_key: document.getElementById('llmApiKey').value.trim(),
    clear_api_key: document.getElementById('llmClearKey').checked,
  };
}

function processingBusy(busy) {
  state.processingSaving = busy;
  const shared = Boolean(state.processing?.settings_shared);
  for (const id of ['btnProcessingRules', 'btnProcessingLlm', 'btnSaveProcessing']) {
    document.getElementById(id).disabled = busy || shared;
  }
  for (const input of document.querySelectorAll('#processingForm input')) input.disabled = busy || shared;
  document.getElementById('btnTestProcessing').disabled = busy;
}

async function saveProcessing(mode) {
  if (state.processingSaving || state.processing?.settings_shared) return;
  processingBusy(true);
  const feedback = document.getElementById('processingFeedback');
  feedback.textContent = '正在保存…';
  try {
    // Switching back to rules stays possible even while the draft form is invalid.
    const payload = mode === 'rules' ? { processing_mode: 'rules' } : processingPayload(mode);
    const result = await apiPost('/api/processing', payload);
    // Discard status snapshots requested before this save completed.
    state.statusRequest++;
    if (mode !== 'rules') state.processingDirty = false;
    renderProcessing(result.processing, mode !== 'rules');
    if (mode !== 'rules') {
      document.getElementById('llmApiKey').value = '';
      document.getElementById('llmClearKey').checked = false;
    }
    feedback.textContent = mode === 'rules' && state.processingDirty
      ? '已切回本地规则，用于后续新消息。表单中其他修改尚未保存，已为你保留；历史记录不补发。'
      : '已持久保存，将持续用于后续所有新消息；无需重复填写密钥。历史记录不补发。';
    showToast('处理配置与对应邮件格式已保存', 'success');
  } catch (error) {
    feedback.textContent = error.message;
    showToast(error.message, 'error');
  } finally {
    processingBusy(false);
  }
}

async function testProcessing() {
  if (state.processingSaving) return;
  processingBusy(true);
  const output = document.getElementById('llmTestResult');
  output.textContent = '正在请求 LLM…';
  try {
    const payload = processingPayload('llm');
    payload.text = document.getElementById('llmTestText').value;
    const result = await apiPost('/api/processing/test', payload);
    output.textContent = result.events.length ? JSON.stringify(result.events.map(event => ({
      标题: event.title, 摘要: event.summary, 活动时间: event.start_at || event.activity_time_text || '未注明',
      报名开始: event.signup_start_at || event.signup_start_text || '未注明', 报名链接: event.signup_url || '未注明',
      处理方式: event.processing_method, 原文: event.source_text,
    })), null, 2) : '未识别到讲座相关内容。测试未入库、未发信。';
  } catch (error) {
    output.textContent = `测试失败：${error.message}`;
  } finally {
    processingBusy(false);
  }
}

// Event Listeners Setup
function setupEventListeners() {
  document.getElementById('btnReturnActive').onclick = () => {
    const active = state.databases.find(db => db.is_active);
    if (active) {
      selectDatabase(active.path);
      loadEvents(); loadMessages(); loadOutbox();
    }
  };
  for (const eventName of ['input', 'change']) {
    document.getElementById('processingForm').addEventListener(eventName, () => {
      state.processingDirty = true;
      document.getElementById('processingFeedback').textContent = '有未保存的修改；点击保存配置后用于后续新消息。';
    });
  }
  document.getElementById('processingForm').addEventListener('submit', (event) => {
    event.preventDefault(); saveProcessing();
  });
  document.getElementById('btnProcessingRules').onclick = () => saveProcessing('rules');
  document.getElementById('btnProcessingLlm').onclick = () => saveProcessing('llm');
  document.getElementById('btnTestProcessing').onclick = testProcessing;

  // Tabs
  document.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.onclick = () => {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

      btn.classList.add('active');
      const tabId = btn.getAttribute('data-tab');
      document.getElementById(tabId).classList.add('active');
      state.currentTab = tabId;

      if (tabId === 'tab-logs') { state._lastLogsText = null; loadLogs(); }
      else if (tabId === 'tab-events') { state._lastEventsRenderKey = null; loadEvents(); }
      else if (tabId === 'tab-messages') loadMessages();
      else if (tabId === 'tab-outbox') { state._lastOutboxRenderKey = null; loadOutbox(); }
      else if (tabId === 'tab-config') { state._lastServicesRenderKey = null; loadServices(); }
    };
  });

  // Filter Pills
  document.querySelectorAll('#tab-events .filter-pill').forEach((pill) => {
    pill.onclick = () => {
      document.querySelectorAll('#tab-events .filter-pill').forEach(p => p.classList.remove('active'));
      pill.classList.add('active');
      state.currentFilter = pill.getAttribute('data-filter');
      state._lastEventsRenderKey = null;
      renderEvents();
    };
  });

  // Search
  document.getElementById('eventSearchInput').oninput = (e) => {
    state.searchQuery = e.target.value;
    state._lastEventsRenderKey = null;
    renderEvents();
  };

  document.querySelectorAll('[data-message-filter]').forEach(button => {
    button.onclick = () => {
      state.messageFilter = button.dataset.messageFilter;
      state.messageOffset = 0;
      document.querySelectorAll('[data-message-filter]').forEach(item => {
        const active = item === button;
        item.classList.toggle('active', active);
        item.setAttribute('aria-pressed', String(active));
      });
      loadMessages();
    };
  });
  document.getElementById('messageSearchInput').oninput = event => {
    state.messageSearch = event.target.value;
    state.messageOffset = 0;
    clearTimeout(state.messageSearchTimer);
    state.messageSearchTimer = setTimeout(loadMessages, 200);
  };
  document.getElementById('btnMessagesPrevious').onclick = () => {
    state.messageOffset = Math.max(0, state.messageOffset - state.messageLimit);
    loadMessages();
  };
  document.getElementById('btnMessagesNext').onclick = () => {
    state.messageOffset += state.messageLimit;
    loadMessages();
  };

  // Header quick switch group
  const btnHeaderSwitch = document.getElementById('btnHeaderSwitchGroup');
  if (btnHeaderSwitch) {
    btnHeaderSwitch.onclick = () => {
      const configTabBtn = document.querySelector('[data-tab="tab-config"]');
      if (configTabBtn) configTabBtn.click();
      const inputEl = document.getElementById('inputSwitchGroup');
      if (inputEl) {
        inputEl.focus();
        inputEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    };
  }

  // Group Switch Candidate Probe
  const btnProbeCandidate = document.getElementById('btnProbeGroupCandidate');
  const feedbackBox = document.getElementById('groupSwitchFeedback');
  if (btnProbeCandidate) {
    btnProbeCandidate.onclick = async () => {
      if (state.isConsoleStopped || state.isConsoleStopping) {
        showToast('控制台正在关闭或已关闭，无法执行群窗口探测', 'info');
        return;
      }
      const targetGroup = (document.getElementById('inputSwitchGroup').value || '').trim();
      if (!targetGroup) {
        showToast('请输入待探测的微信群名称', 'error');
        return;
      }

      feedbackBox.style.display = 'block';
      feedbackBox.className = 'feedback-box';
      feedbackBox.textContent = `正在探测目标群【${targetGroup}】独立窗口，请保持微信该群窗口打开并可见...`;
      btnProbeCandidate.disabled = true;

      try {
        const res = await apiGet(`/api/group/probe?group=${encodeURIComponent(targetGroup)}`);
        if (res.ok) {
          feedbackBox.className = 'feedback-box feedback-success';
          feedbackBox.textContent = `✓ 探测成功：已找到群【${targetGroup}】的独立聊天窗口并可读。您可以点击【2. 确认并切换群】进行配置保存与切换。`;
          showToast(`目标群【${targetGroup}】窗口已就绪`, 'success');
        } else {
          feedbackBox.className = 'feedback-box feedback-warning';
          feedbackBox.textContent = `⚠️ 未探测到群【${targetGroup}】的聊天窗口。请先在微信中将该群双击打开为独立窗口并保持可见。系统已为您保留原群配置。`;
          showToast('未找到该群的可见独立窗口', 'error');
        }
      } catch (err) {
        feedbackBox.className = 'feedback-box feedback-error';
        feedbackBox.textContent = `探测请求异常: ${err.message}`;
        showToast(`探测出错: ${err.message}`, 'error');
      } finally {
        if (!state.isConsoleStopped) {
          btnProbeCandidate.disabled = false;
        }
      }
    };
  }

  // Execute Group Switch
  const btnExecuteSwitch = document.getElementById('btnExecuteGroupSwitch');
  if (btnExecuteSwitch) {
    btnExecuteSwitch.onclick = () => {
      if (state.isConsoleStopped || state.isConsoleStopping) {
        showToast('控制台正在关闭或已关闭，无法执行群切换', 'info');
        return;
      }
      const targetGroup = (document.getElementById('inputSwitchGroup').value || '').trim();
      if (!targetGroup) {
        showToast('请输入微信群完整精确名称', 'error');
        return;
      }

      showConfirm(
        '确认切换监测微信群',
        `确定要将监控群切换为「${targetGroup}」吗？\n\n系统将自动停止当前正在运行的监控和自启动，并为该群分配独立的稳定数据目录。切换操作保留原群历史，切回时可继续使用。\n\n切换后服务将处于安全停止状态，需在确认后手动启动预览。`,
        async () => {
          btnExecuteSwitch.disabled = true;
          feedbackBox.style.display = 'block';
          feedbackBox.className = 'feedback-box';
          feedbackBox.textContent = `正在切换至群【${targetGroup}】，停止旧服务并原子保存配置...`;

          try {
            const res = await apiPost('/api/group/switch', { group: targetGroup });
            if (!res.ok) {
              feedbackBox.className = 'feedback-box feedback-warning';
              feedbackBox.textContent = res.error || '切换未通过，已保留原配置';
              showToast(res.error || '群切换未通过', 'error');
            } else {
              feedbackBox.className = 'feedback-box feedback-success';
              feedbackBox.textContent = `✓ ${res.message} (分配数据目录: ${res.data_dir})`;
              showToast(`已成功切换至群【${targetGroup}】`, 'success');

              // 切群成功后把 state.selectedDb 清空，再 await loadDatabases 并选中返回的当前活动库，禁止继续显示旧群库
              selectDatabase('');
              state._lastEventsRenderKey = null;
              state._lastOutboxRenderKey = null;
              state._lastServicesRenderKey = null;
              await refreshStatus();
              await loadDatabases();
              await loadEvents();
              state.messageOffset = 0;
              await loadMessages();
              await loadOutbox();
              await loadServices();
            }
          } catch (err) {
            feedbackBox.className = 'feedback-box feedback-error';
            feedbackBox.textContent = `切换执行失败: ${err.message}`;
            showToast(`切换失败: ${err.message}`, 'error');
          } finally {
            if (!state.isConsoleStopped) {
              btnExecuteSwitch.disabled = false;
            }
          }
        }
      );
    };
  }

  // Refresh services button
  const btnRefServices = document.getElementById('btnRefreshServices');
  if (btnRefServices) {
    btnRefServices.onclick = () => {
      state._lastServicesRenderKey = null;
      loadServices();
      showToast('服务列表已刷新', 'info');
    };
  }

  // Service Controls
  const btnAuthorizeEmail = document.getElementById('btnAuthorizeEmail');
  if (btnAuthorizeEmail) {
    btnAuthorizeEmail.onclick = async () => {
      if (!state.emailStatus?.configured) {
        showToast('邮箱尚未配置，请按下方邮箱设置中的说明完成首次配置', 'info', 6000);
        document.getElementById('emailSetupHelp').scrollIntoView({ behavior: 'smooth', block: 'center' });
        return;
      }
      btnAuthorizeEmail.disabled = true;
      const macKeychain = state.emailStatus?.backend === 'macos_keychain';
      showToast(macKeychain ? '请在系统钥匙串弹窗中完成授权并选择“始终允许”' : '正在检查本机邮箱凭据…', 'info');
      try {
        const result = await apiPost('/api/email/authorize', {});
        if (!result.configured) throw new Error('尚未配置邮箱，请按下方终端命令完成首次设置');
        showToast(macKeychain ? '邮箱授权完成，后台会自动重新加载；未发送测试邮件' : '本机邮箱凭据检查通过；未发送测试邮件', 'success');
        await refreshStatus();
      } catch (err) {
        showToast(`${macKeychain ? '邮箱授权未完成' : '邮箱检查未通过'}：${err.message}`, 'error');
      } finally {
        btnAuthorizeEmail.disabled = false;
      }
    };
  }
  document.getElementById('btnStartService').onclick = () => controlService('start', false);
  document.getElementById('btnStartSendService').onclick = () => confirmSendService('start');
  document.getElementById('btnStopService').onclick = () => {
    showConfirm('停止微信监控', '确定要停止微信讲座监控服务吗？监控停止后将暂停捕获新消息，网页控制台仍可继续查看历史数据。', () => {
      controlService('stop');
    });
  };
  document.getElementById('btnRestartService').onclick = () => state.service.send_enabled ? confirmSendService('restart') : controlService('restart');

  // Close Web Console (Stop UI backend server while keeping WeChat monitoring untouched)
  const btnCloseConsole = document.getElementById('btnCloseConsole');
  if (btnCloseConsole) {
    btnCloseConsole.onclick = stopConsole;
  }

  // Probe Buttons
  document.getElementById('btnProbe').onclick = () => {
    document.querySelector('[data-tab="tab-probe"]').click();
    runProbe();
  };
  document.getElementById('btnRunProbeAction').onclick = runProbe;

  // Tick Button
  document.getElementById('btnTick').onclick = async () => {
    if (state.isConsoleStopped || state.isConsoleStopping) {
      showToast('控制台正在关闭或已关闭，无法触发调度', 'info');
      return;
    }
    const btn = document.getElementById('btnTick');
    btn.disabled = true;
    showToast(`正在预览管理群「${state.config.group || '当前群'}」的到期任务…`, 'info');
    try {
      const res = await apiPost('/api/tick');
      showToast(`到期任务预览完成：${res.dispatched || 0} 条；本次操作未发信`, 'success');
      await loadOutbox();
    } catch (err) {
      showToast(`调度失败: ${err.message}`, 'error');
    } finally {
      if (!state.isConsoleStopped) {
        btn.disabled = false;
      }
    }
  };

  // Export ICS
  document.getElementById('btnExportIcs').onclick = () => {
    if (state.isConsoleStopped || state.isConsoleStopping) {
      showToast('控制台正在关闭或已关闭，无法导出日历', 'info');
      return;
    }
    const btn = document.getElementById('btnExportIcs');
    btn.disabled = true;
    const url = state.selectedDb ? `/api/calendar/export?db=${encodeURIComponent(state.selectedDb)}` : '/api/calendar/export';
    const a = document.createElement('a');
    a.href = url;
    a.download = 'lectures.ics';
    document.body.appendChild(a);
    a.click();
    a.remove();
    showToast('正在导出 ICS 日历文件...', 'success');
    setTimeout(() => {
      if (!state.isConsoleStopped) {
        btn.disabled = false;
      }
    }, 1200);
  };

  // Demo runner: Demo API 返回 db 路径，演示后自动选择并明确标记为 Demo 只读
  document.getElementById('btnRunDemo').onclick = async () => {
    if (state.isConsoleStopped || state.isConsoleStopping) {
      showToast('控制台正在关闭或已关闭，无法运行 Demo 回放', 'info');
      return;
    }
    const btn = document.getElementById('btnRunDemo');
    btn.disabled = true;
    showToast('正在运行截图样例回放测试...', 'info');
    try {
      const res = await apiPost('/api/demo/run');
      showToast('Demo 回放完成，生成了隔离演示数据', 'success');
      if (res && res.db) {
        selectDatabase(res.db);
      }
      await loadDatabases();
      if (res && res.db) {
        selectDatabase(res.db);
        const select = document.getElementById('databaseSelect');
        if (select) select.value = res.db;
        updateDbTypeBadge();
      }
      await loadEvents();
      state.messageOffset = 0;
      await loadMessages();
      await loadOutbox();
      document.querySelector('[data-tab="tab-events"]').click();
    } catch (err) {
      showToast(`Demo 运行失败: ${err.message}`, 'error');
    } finally {
      if (!state.isConsoleStopped) {
        btn.disabled = false;
      }
    }
  };

  // Logs controls
  document.getElementById('btnRefreshLogs').onclick = () => {
    state._lastLogsText = null;
    loadLogs();
  };
  document.getElementById('btnClearLogs').onclick = () => {
    state._lastLogsText = '';
    document.getElementById('terminalBody').innerHTML = '';
  };
  document.getElementById('chkAutoScrollLogs').onchange = (e) => {
    state.autoScrollLogs = e.target.checked;
  };

  // Refresh
  document.getElementById('manualRefreshBtn').onclick = () => {
    state._lastEventsRenderKey = null;
    state._lastOutboxRenderKey = null;
    state._lastServicesRenderKey = null;
    state._lastLogsText = null;
    refreshStatus();
    loadEvents();
    loadMessages();
    loadOutbox();
    loadServices();
    if (state.currentTab === 'tab-logs') loadLogs();
    showToast('正在刷新数据...', 'info');
  };

  document.getElementById('autoRefreshSelect').onchange = (e) => {
    const val = parseInt(e.target.value, 10);
    state.refreshInterval = val;
    startRefreshTimer();
    if (val > 0) {
      showToast(`自动刷新已设置为每 ${val / 1000} 秒`, 'info');
    } else {
      showToast('已关闭自动刷新', 'info');
    }
  };

  // Tab visibility: pause periodic refresh when hidden to save CPU and power
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      if (state.refreshTimer) {
        clearInterval(state.refreshTimer);
        state.refreshTimer = null;
      }
    } else {
      if (!state.isConsoleStopped && !state.isConsoleStopping && state.refreshInterval > 0) {
        runPeriodicRefresh();
        startRefreshTimer();
      }
    }
  });

  // Modal Closers
  document.querySelectorAll('.close-modal-btn').forEach((btn) => {
    btn.onclick = () => {
      closeModal(btn.closest('.modal-overlay'));
    };
  });

  document.querySelectorAll('.modal-overlay').forEach((modal) => {
    modal.onclick = (e) => {
      if (e.target === modal) closeModal(modal);
    };
  });

  document.addEventListener('keydown', (e) => {
    const activeModals = Array.from(document.querySelectorAll('.modal-overlay.active'));
    const modal = activeModals[activeModals.length - 1];
    if (!modal) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      closeModal(modal);
    } else if (e.key === 'Tab') {
      const focusable = Array.from(modal.querySelectorAll('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), iframe, [tabindex="0"]'))
        .filter(element => element.getClientRects().length);
      const first = focusable[0] || modal;
      const last = focusable[focusable.length - 1] || modal;
      if (e.shiftKey && (document.activeElement === first || !modal.contains(document.activeElement))) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && (document.activeElement === last || !modal.contains(document.activeElement))) {
        e.preventDefault(); first.focus();
      }
    }
  });

  // Event detail modal preview email button
  document.getElementById('modalPreviewEmailBtn').onclick = () => {
    if (state.currentModalEvent) {
      openEmailPreview(state.currentModalEvent.id);
    }
  };

  // Theme Toggle
  const themeBtn = document.getElementById('themeToggleBtn');
  const savedTheme = localStorage.getItem('wechat_tracker_theme');
  if (savedTheme) {
    document.documentElement.setAttribute('data-theme', savedTheme);
  }

  themeBtn.onclick = () => {
    const current = document.documentElement.getAttribute('data-theme') ||
      (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('wechat_tracker_theme', next);
    showToast(`已切换至${next === 'dark' ? '暗色' : '亮色'}主题`, 'info');
  };
}

// App Initialization
async function initApp() {
  setupEventListeners();

  // Initial loads with safe fallback
  try { await refreshStatus(); } catch (e) { console.warn('refreshStatus error:', e); }
  try { await loadDatabases(); } catch (e) { console.warn('loadDatabases error:', e); }
  try { await loadEvents(); } catch (e) { console.warn('loadEvents error:', e); }
  try { await loadMessages(); } catch (e) { console.warn('loadMessages error:', e); }
  try { await loadOutbox(); } catch (e) { console.warn('loadOutbox error:', e); }
  try { await loadServices(); } catch (e) { console.warn('loadServices error:', e); }

  startRefreshTimer();
}

document.addEventListener('DOMContentLoaded', initApp);
