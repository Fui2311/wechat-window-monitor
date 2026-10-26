<script setup>
import { ref, onMounted, onBeforeUnmount } from "vue";
import { useRequests } from "../lib/scope";
import { endpoint } from "../lib/api";
const props = defineProps({ window: Object, prompt: String });
const emit = defineEmits(["close", "imported"]);
const request = useRequests();
const context = { ...props.window };
const exporting = props.prompt !== undefined;
const dialog = ref(null),
  textarea = ref(null),
  content = ref(props.prompt || ""),
  busy = ref(false),
  error = ref(""),
  notice = ref("");
let prior;
async function copy() {
  try {
    await navigator.clipboard.writeText(content.value);
    notice.value = "提示词已复制到剪贴板，请粘贴给网页版 AI。";
  } catch {
    notice.value = "自动复制未成功，已选中提示词，请按 ⌘C / Ctrl+C 复制。";
    textarea.value?.focus();
    textarea.value?.select();
  }
}
onMounted(() => {
  prior = document.activeElement;
  dialog.value.showModal();
  if (exporting) copy();
});
onBeforeUnmount(() => prior?.focus());
async function importRule() {
  busy.value = true;
  error.value = "";
  try {
    const data = await request(endpoint(context.id, "import-rule"), {
      content: content.value,
      revision: context.revision,
    });
    emit("imported", data.rule);
  } catch (e) {
    if (e.name !== "AbortError") error.value = e.message;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <dialog
    ref="dialog"
    class="modal"
    aria-labelledby="rule-exchange-title"
    @cancel.prevent="!busy && emit('close')"
  >
    <form @submit.prevent="exporting ? copy() : importRule()">
      <header>
        <h2 id="rule-exchange-title">
          {{ exporting ? "导出本地规则提示词" : "导入网页 AI 结果" }}
        </h2>
        <button
          type="button"
          class="icon-button"
          aria-label="关闭规则交换弹窗"
          :disabled="busy"
          @click="emit('close')"
        >
          ×
        </button>
      </header>
      <p class="muted">
        {{
          exporting
            ? "复制到你使用的网页版 AI，得到完整 JSON 后，回到本页点击“导入网页 AI 结果”。提示词只包含识别目标与规则格式。"
            : "粘贴网页版 AI 返回的完整 JSON 对象或单个 JSON 代码块。校验通过后保存为新草稿，试测并启用后生效。"
        }}
      </p>
      <p><strong>当前识别目标：</strong>{{ context.target }}</p>
      <label>
        {{ exporting ? "提示词内容" : "AI 生成的规则 JSON" }}
        <textarea
          ref="textarea"
          v-model="content"
          class="code-editor"
          rows="14"
          spellcheck="false"
          :readonly="exporting"
          :disabled="busy"
          :maxlength="exporting ? undefined : 100000"
          :required="!exporting"
          :autofocus="!exporting"
          :aria-label="exporting ? '导出提示词' : '导入规则JSON'"
          :placeholder="exporting ? '' : '在此粘贴网页版 AI 返回的 JSON'"
        ></textarea>
      </label>
      <p v-if="notice" class="notice" role="status">{{ notice }}</p>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <footer>
        <button type="button" :disabled="busy" @click="emit('close')">
          {{ exporting ? "完成" : "取消" }}
        </button>
        <button class="primary" :disabled="busy || !content.trim()">
          {{ exporting ? "再次复制提示词" : busy ? "正在校验…" : "导入为草稿" }}
        </button>
      </footer>
    </form>
  </dialog>
</template>
