/** Pure console presentation rules. No DOM, storage or network access. */
const TrackerPresentation = (() => {
  'use strict';
  const dateFormatters = new Map();

  function dateParts(isoString, zone = 'Asia/Shanghai') {
    if (!isoString) return null;
    try {
      const date = new Date(isoString);
      if (isNaN(date.getTime())) return null;
      if (!dateFormatters.has(zone)) dateFormatters.set(zone, new Intl.DateTimeFormat('en-GB', {
        timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
      }));
      return Object.fromEntries(dateFormatters.get(zone).formatToParts(date).map(part => [part.type, part.value]));
    } catch (error) {
      return null;
    }
  }

  function formatTime(isoString, zone = 'Asia/Shanghai') {
    const parts = dateParts(isoString, zone);
    return parts ? `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}` : '';
  }

  function displayEventTime(isoString, rawText, zone = 'Asia/Shanghai') {
    const formatted = formatTime(isoString, zone);
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

  function formatClockTime(isoString, zone = 'Asia/Shanghai') {
    if (!isoString) return '--';
    const parts = dateParts(isoString, zone);
    return parts ? `${parts.hour}:${parts.minute}:${parts.second}` : String(isoString);
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

  function formatCaptureTime(value, zone = 'Asia/Shanghai') {
    const parts = dateParts(value, zone);
    return parts ? `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}` : '未记录';
  }

  function serviceStatusView(service) {
    if (service.status === 'database_error') return {label:'数据库异常', summary:'数据库异常', level:'danger', color:'red'};
    if (!service.running) return service.status === 'stopped_or_unresponsive'
      ? {label:'停止或无响应', summary:'状态待核对', level:'danger', color:'red'}
      : {label:'服务未启动', summary:'已停止', level:'danger', color:'red'};
    if (service.status === 'monitoring') return {label:'监控中', summary:'监控运行中', level:'success', color:'green'};
    if (service.status === 'waiting_for_weixin') return {label:'等待微信窗口', summary:'等待微信', level:'warning', color:'amber'};
    if (service.status === 'starting') return {label:'服务启动中', summary:'启动中', level:'warning', color:'amber'};
    return {label:'采集待确认', summary:'运行待核对', level:'warning', color:'amber'};
  }

  return Object.freeze({formatTime, formatClockTime, formatCaptureTime, displayEventTime,
    escapeHtml, signupLinkHtml, serviceStatusView});
})();

if (typeof module !== 'undefined' && module.exports) module.exports = TrackerPresentation;
