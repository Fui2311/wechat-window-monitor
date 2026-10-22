<script setup>
import { ref } from "vue";
import { useRequests } from "../lib/scope";
import { endpoint } from "../lib/api";
import Hint from "./Hint.vue";
const props = defineProps({ window: Object });
const emit = defineEmits(["refresh"]);
const request = useRequests();
const form = ref(
    Object.fromEntries(
      [
        "name",
        "target",
        "mode",
        "interval",
        "include_existing",
        "capture",
        "revision",
      ].map((k) => [k, JSON.parse(JSON.stringify(props.window[k]))]),
    ),
  ),
  busy = ref(false),
  error = ref(""),
  notice = ref("");
async function save() {
  busy.value = true;
  error.value = "";
  try {
    const d = await request(endpoint(props.window.id), form.value);
    form.value.revision = d.window.revision;
    notice.value = "配置已保存。修改目标或识别方式后，请重新生成并启用规则。";
    emit("refresh");
  } catch (e) {
    if (e.name !== "AbortError") error.value = e.message;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <form class="panel settings-panel" @submit.prevent="save">
    <span class="eyebrow">WINDOW SETTINGS</span>
    <h2>窗口与采集</h2>
    <p class="muted">配置只作用于当前窗口，保存不会自动开始监控。</p>
    <fieldset :disabled="busy || window.runtime.running">
      <div class="form-grid">
        <label
          >窗口精确名称
          <Hint
            text="必须完整匹配独立窗口标题。若有同名窗口，采集会拒绝猜测。" /><input
            v-model="form.name"
            required
            maxlength="200" /></label
        ><label
          >识别方式<select v-model="form.mode">
            <option value="local">本地匹配</option>
            <option value="llm">LLM语义识别</option>
          </select></label
        ><label class="full"
          >识别目标<textarea
            v-model="form.target"
            required
            maxlength="4000"
            rows="3"
          ></textarea></label
        ><label
          >采集来源<select v-model="form.capture.source">
            <option value="macos_ax">macOS Accessibility</option>
            <option value="windows_uia">Windows UIAutomation</option>
            <option value="jsonl">离线 JSONL</option>
          </select></label
        ><label
          >采样间隔（秒）
          <Hint
            text="1至300秒。成功采样才表示窗口可读；新入库消息才会触发识别。" /><input
            v-model.number="form.interval"
            type="number"
            min="1"
            max="300"
            required /></label
        ><label v-if="form.capture.source === 'macos_ax'" class="full"
          >原生采集辅助程序路径<input
            v-model="form.capture.mac_helper"
            placeholder="/完整路径/wechat-capture" /></label
        ><label v-if="form.capture.source === 'jsonl'" class="full"
          >JSONL 文件路径<input
            v-model="form.capture.jsonl_path"
            required
            placeholder="/完整路径/messages.jsonl" /></label
        ><label class="check full"
          ><input
            type="checkbox"
            v-model="form.include_existing" />首次分析当前可见消息
          <Hint
            text="默认关闭，首次采样只记录基线。开启后首次可见消息也会按活动规则识别；已保存的历史不因切换而重算。"
        /></label>
      </div>
      <details>
        <summary>高级原生绑定</summary>
        <div class="form-grid">
          <label
            >macOS 消息列表标识<input
              v-model="form.capture.mac_list_identifier"
              placeholder="留空使用默认绑定" /></label
          ><label
            >macOS 标题控件标识<input
              v-model="form.capture.mac_header_identifier"
              placeholder="留空按完整窗口标题绑定" /></label
          ><label
            >Windows 微信进程名<input
              v-model="form.capture.process_name"
              placeholder="默认 Weixin"
          /></label>
        </div>
      </details>
      <button class="primary">{{ busy ? "正在保存…" : "保存窗口配置" }}</button>
    </fieldset>
    <p v-if="window.runtime.running" class="notice">
      先停止监控并等待停止完成，再修改设置。
    </p>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </form>
</template>
