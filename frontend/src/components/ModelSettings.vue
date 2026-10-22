<script setup>
import { ref } from "vue";
import { useRequests } from "../lib/scope";
import Hint from "./Hint.vue";
const props = defineProps({ model: Object });
const emit = defineEmits(["refresh"]);
const request = useRequests();
const form = ref({ ...props.model, api_key: "", clear_key: false }),
  busy = ref(false),
  error = ref(""),
  notice = ref("");
async function save() {
  busy.value = true;
  error.value = "";
  try {
    const { key_configured, ...body } = form.value;
    const d = await request("/api/model", body);
    form.value = { ...d.model, api_key: "", clear_key: false };
    notice.value = "模型设置已保存，密钥不会返回页面。";
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
    <span class="eyebrow">MODEL CONNECTION</span>
    <h2>模型设置</h2>
    <p class="muted">
      所有窗口共用此兼容 Chat Completions
      接口。生成规则与语义识别使用这里的模型。
    </p>
    <fieldset :disabled="busy">
      <div class="form-grid">
        <label class="full"
          >接口基础地址
          <Hint
            text="远程接口需HTTPS，本机服务可用http://127.0.0.1。请勿把密钥放进地址。" /><input
            v-model="form.base_url"
            required
            type="url"
            placeholder="https://api.example.com/v1" /></label
        ><label
          >模型名称<input
            v-model="form.model"
            required
            placeholder="接口提供的模型标识" /></label
        ><label
          >超时（秒）<input
            v-model.number="form.timeout"
            required
            type="number"
            min="1"
            max="60" /></label
        ><label
          >API Key
          <Hint
            text="留空保留现有密钥。存放在当前数据目录的独立私有文件，或使用环境变量；不随项目上传。" /><input
            v-model="form.api_key"
            type="password"
            autocomplete="new-password"
            :placeholder="
              form.key_configured ? '已配置 · 留空保留' : '仅在需要时填写'
            " /></label
        ><label>密钥环境变量<input v-model="form.api_key_env" required /></label
        ><label class="check full"
          ><input
            v-model="form.clear_key"
            type="checkbox"
          />清除已保存的密钥文件</label
        >
      </div>
      <button class="primary">{{ busy ? "正在保存…" : "保存模型设置" }}</button>
    </fieldset>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p class="muted">
      保存设置不调用模型。规则生成发送目标，语义识别发送所选窗口中新入库正文；本地模式日常识别不访问模型。
    </p>
  </form>
</template>
