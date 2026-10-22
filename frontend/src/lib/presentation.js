const names = {
  stopped: "已停止",
  stopping: "正在停止",
  starting: "正在启动",
  monitoring: "正在监控",
  waiting: "等待窗口",
  recognition_error: "识别异常",
};
export function statusView(runtime = {}) {
  if (runtime.unavailable) return { label: "状态不可用", color: "gray" };
  const key = runtime.running ? runtime.status : "stopped";
  return {
    label: names[key] || "状态未知",
    color:
      key === "monitoring"
        ? "green"
        : ["waiting", "recognition_error"].includes(key)
          ? "amber"
          : "gray",
  };
}
export const modeName = (mode) => (mode === "llm" ? "语义识别" : "本地匹配");
export function timeLabel(value) {
  if (!value) return "尚无记录";
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? value
    : new Intl.DateTimeFormat("zh-CN", {
        dateStyle: "short",
        timeStyle: "medium",
      }).format(d);
}
export function bodyText(message, depth = 0) {
  if (depth > 6 || !message) return "";
  return [
    message.text,
    ...(message.children || []).map((x) => bodyText(x, depth + 1)),
    ...(message.attachments || []).map((x) =>
      [x.name, x.text].filter(Boolean).join(" "),
    ),
  ]
    .filter(Boolean)
    .join("\n");
}
