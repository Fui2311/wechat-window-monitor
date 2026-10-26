<script setup>
import { ref, onMounted, onBeforeUnmount } from "vue";
import { useRequests } from "../lib/scope";
import Hint from "./Hint.vue";
const props = defineProps({ defaultCapture: Object });
const emit = defineEmits(["close", "created"]);
const request = useRequests();
const dialog = ref(null),
  busy = ref(false),
  error = ref("");
const form = ref({
  name: "",
  target: "",
  mode: "local",
  capture: { ...(props.defaultCapture || { source: "windows_uia" }) },
});
let prior;
onMounted(() => {
  prior = document.activeElement;
  dialog.value.showModal();
});
onBeforeUnmount(() => prior?.focus());
async function create() {
  busy.value = true;
  error.value = "";
  try {
    const data = await request("/api/windows", form.value);
    emit("created", data.window);
  } catch (e) {
    if (e.name !== "AbortError") error.value = e.message;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <dialog ref="dialog" class="modal" @cancel.prevent="!busy && emit('close')">
    <form @submit.prevent="create">
      <header>
        <div>
          <span class="eyebrow">NEW WINDOW</span>
          <h2>添加窗口监控</h2>
        </div>
        <button
          type="button"
          class="icon-button"
          @click="emit('close')"
          :disabled="busy"
          aria-label="关闭新增窗口"
        >
          ×
        </button>
      </header>
      <p class="muted">先填写名称与识别目标，创建后再完善采集设置。</p>
      <label
        >微信窗口精确名称
        <Hint
          text="复制独立会话窗口的完整标题。群聊、私聊均可；重名或结构不可读时会拒绝采集。" /><input
          v-model="form.name"
          required
          maxlength="200"
          autofocus
          placeholder="例如：项目讨论、文件传输助手" /></label
      ><label
        >你希望关注什么
        <Hint
          text="描述要提醒的内容与排除条件。本地模式可用 API 生成，也可导出提示词交给网页 AI 后导入规则。"
        /><textarea
          v-model="form.target"
          required
          maxlength="4000"
          rows="3"
          placeholder="例如：提醒项目发布失败、服务中断；忽略恢复成功和日常讨论"
        ></textarea></label
      ><label
        >识别方式<select v-model="form.mode">
          <option value="local">生成本地规则 · 后续零模型调用</option>
          <option value="llm">生成语义规则 · 后续由 LLM 判断</option>
        </select></label
      >
      <details :open="form.capture.source === 'jsonl'">
        <summary>采集来源（可稍后完善）</summary>
        <label
          >采集平台<select v-model="form.capture.source">
            <option value="macos_ax">macOS Accessibility</option>
            <option value="windows_uia">Windows UIAutomation</option>
            <option value="jsonl">离线 JSONL</option>
          </select></label
        ><label v-if="form.capture.source === 'jsonl'"
          >JSONL 文件路径<input v-model="form.capture.jsonl_path" required
        /></label>
      </details>
      <p class="notice quiet">
        创建后保持停止。生成或导入规则并启用后，可手动开始监控。语义模式需配置模型。
      </p>
      <p v-if="error" role="alert" class="error">{{ error }}</p>
      <footer>
        <button type="button" @click="emit('close')" :disabled="busy">
          取消</button
        ><button class="primary" :disabled="busy">
          {{ busy ? "正在创建…" : "创建窗口" }}
        </button>
      </footer>
    </form>
  </dialog>
</template>
