"""Public status projection and delivery labels; never loads credentials."""
from datetime import datetime, timezone

def _delivery_info(job: dict, service: dict, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    status, attempts = job.get("status"), job.get("attempts", 0)
    result = dict(state=status, attempts=attempts, error=job.get("last_error"), retry_at=None)
    def done(state, label, message):
        return dict(result, state=state, label=label, message=message)
    if status == "sent":
        return done("sent", "已提交 SMTP", "邮件服务器已接受；若未出现在收件箱，请检查垃圾邮件或稍后刷新邮箱。")
    if status == "dead":
        return done("failed", "发送失败，已停止重试", "已达到最大尝试次数，请检查邮箱配置和最近错误。")
    if status in {"superseded", "expired", "coalesced"}:
        return done(status, "已取消此任务", "此邮件任务已被替代、过期或合并，不会继续发送。")
    if status == "sending":
        return done("sending", "正在发送", "正在连接邮件服务器或提交邮件，请稍候。")
    if status != "pending":
        return done(status or "unknown", "状态待核对", "请查看邮件队列与服务状态。")
    if not service.get("running"):
        return done("blocked_stopped", "等待服务启动", "通知已入队，所属群的监控服务未运行。")
    if not service.get("send_enabled"):
        return done("preview_only", "仅预览，未发送", "所属服务处于预览模式，启用发信模式后才会投递。")
    if not service.get("send_ready"):
        message = ("Keychain 邮箱尚未授权；请在配置与邮箱设置中点击“授权邮箱”，后台不会主动弹窗。" if service.get("email_state") == "credential_access_failed"
                   else "邮箱凭据未就绪，尚未尝试发送；配置恢复后会自动继续投递。")
        return done("blocked_credentials", "等待邮箱配置", message)
    try:
        due = datetime.fromisoformat(job["due_at"].replace("Z", "+00:00"))
        future = due > now
    except (KeyError, ValueError, TypeError):
        future, due = False, now
    if attempts and future:
        result["retry_at"] = job["due_at"]
        return done("retry_wait", "发送失败，等待重试", "上次发送未成功，将按下方时间自动重试。")
    if future:
        return done("scheduled", "等待计划时间", "尚未到计划投递时间。")
    if (now - due).total_seconds() > 30:
        return done("queued_late", "排队时间较长", "已等待超过30秒，请检查所属服务是否繁忙或调度异常。")
    return done("queued", "等待调度", "通常在下一轮调度投递，默认间隔约5秒。")

SAFE_SERVICE_FIELDS = {
    "instance",
    "running",
    "status",
    "pid",
    "started_at",
    "heartbeat_at",
    "group",
    "send_enabled",
    "worker_pid",
    "worker_restarts",
    "snapshots",
    "observed_messages",
    "events_created",
    "email_sent",
    "email_failures",
    "last_capture_at",
    "last_error",
    "capture_gaps",
    "sqlite_version",
    "journal_mode",
    "backup_at",
    "coverage",
    "email_state",
    "send_ready",
    "due_previews",
    "stopped_at",
    "data_dir",
    "config_path",
    "is_current",
    "reset_supported",
    "reset_at",
    "processing_mode", "processing_model", "processing_settings_shared",
    "processing_last_at", "processing_last_method", "processing_last_error",
}


def _sanitize_service(status_dict: dict, extra: dict | None = None) -> dict:
    if not isinstance(status_dict, dict):
        return {}
    sanitized = {k: v for k, v in status_dict.items() if k in SAFE_SERVICE_FIELDS}
    if extra:
        for k, v in extra.items():
            if k in SAFE_SERVICE_FIELDS:
                sanitized[k] = v
    return sanitized
