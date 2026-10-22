<script setup>
import { ref } from "vue";
import { useRequests } from "../lib/scope";
import { endpoint } from "../lib/api";
import { statusView, modeName, timeLabel } from "../lib/presentation";
import Hint from "./Hint.vue";
const props = defineProps({ window: Object });
const emit = defineEmits(["refresh", "tab"]);
const request = useRequests();
const busy = ref(false),
  error = ref(""),
  probe = ref("");
async function action(name) {
  busy.value = true;
  error.value = "";
  try {
    const data = await request(endpoint(props.window.id, name), {});
    if (data.probe)
      probe.value = data.probe.ok
        ? `精确窗口可读 · ${data.probe.message_count} 条可见消息`
        : `窗口未就绪：${data.probe.status}`;
    emit("refresh");
  } catch (e) {
    if (e.name !== "AbortError") error.value = e.message;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="stats">
    <article class="stat">
      <small
        >监控状态
        <Hint
          text="绿点表示正在运行且已成功采样；灰点表示停止或正在启停；异常会显示具体状态。" /></small
      ><strong
        ><i class="dot" :class="statusView(window.runtime).color"></i
        >{{ statusView(window.runtime).label }}</strong
      ><span>{{ timeLabel(window.runtime.last_capture) }}</span>
    </article>
    <article class="stat">
      <small>已保存消息</small
      ><strong>{{
        Object.entries(window.counts)
          .filter(([k]) => k !== "matched")
          .reduce((sum, [, n]) => sum + n, 0)
      }}</strong
      ><span>包含首次基线与待识别记录</span>
    </article>
    <article class="stat">
      <small>匹配提醒</small><strong>{{ window.counts.matched || 0 }}</strong
      ><span
        >{{ modeName(window.mode) }} ·
        {{ window.counts.error || 0 }} 条失败</span
      >
    </article>
  </div>
  <section class="panel">
    <div class="section-heading">
      <div>
        <span class="eyebrow">CURRENT TARGET</span>
        <h2>只关注你需要的内容</h2>
      </div>
      <span class="badge">{{ modeName(window.mode) }}</span>
    </div>
    <p class="target-text">{{ window.target }}</p>
    <div class="status-note" v-if="window.runtime.error" role="status">
      {{ window.runtime.error }}
    </div>
    <div class="actions">
      <button
        class="primary"
        :disabled="busy || window.runtime.running || !window.active_rule"
        @click="action('start')"
      >
        开始监控</button
      ><button
        :disabled="busy || !window.runtime.running"
        @click="action('stop')"
      >
        停止监控</button
      ><button :disabled="busy" @click="action('probe')">检查窗口</button>
      <Hint
        text="只读探测精确名称及消息列表，不导入消息、不调用模型。"
      /><button @click="emit('tab', 'rules')">
        {{ window.active_rule ? "查看规则" : "生成并启用规则 →" }}
      </button>
    </div>
    <p v-if="probe" class="notice" role="status">{{ probe }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </section>
  <section class="panel guide">
    <span class="eyebrow">WORKFLOW</span>
    <h2>一个目标，一套可复用的规则</h2>
    <div class="steps">
      <div>
        <b>01</b>
        <h3>配置窗口</h3>
        <p>精确名称、采集来源和采样间隔。首次采样默认只建立基线。</p>
      </div>
      <div>
        <b>02</b>
        <h3>生成与试测</h3>
        <p>模型生成一次草稿，核对规则、试测样例，再明确启用版本。</p>
      </div>
      <div>
        <b>03</b>
        <h3>开始关注</h3>
        <p>本地规则直接匹配；语义模式复用规则调用模型。结果与原文一起留存。</p>
      </div>
    </div>
    <p class="muted">
      只采集已加载、可读取的微信会话内容。当前控制台进程退出后监控结束，重新启动默认停止。
    </p>
  </section>
</template>
