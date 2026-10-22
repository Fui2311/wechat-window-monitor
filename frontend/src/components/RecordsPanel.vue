<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from "vue";
import { useRequests } from "../lib/scope";
import { endpoint } from "../lib/api";
import { timeLabel, bodyText } from "../lib/presentation";
import Hint from "./Hint.vue";
const props = defineProps({ window: Object });
const emit = defineEmits(["refresh"]);
const request = useRequests();
const records = ref([]),
  query = ref(""),
  category = ref("all"),
  pages = ref([null]),
  page = ref(0),
  next = ref(null),
  more = ref(false),
  loading = ref(false),
  error = ref("");
let epoch = 0,
  timer,
  refresh;
async function load(reset = false) {
  if (reset) {
    pages.value = [null];
    page.value = 0;
  }
  const token = ++epoch;
  loading.value = true;
  error.value = "";
  const params = new URLSearchParams({
    q: query.value,
    category: category.value,
  });
  if (pages.value[page.value]) params.set("before", pages.value[page.value]);
  try {
    const data = await request(
      endpoint(props.window.id, "records") + "?" + params,
    );
    if (token !== epoch) return;
    records.value = data.records;
    more.value = data.has_more;
    next.value = data.next_before;
  } catch (e) {
    if (token === epoch && e.name !== "AbortError") error.value = e.message;
  } finally {
    if (token === epoch) loading.value = false;
  }
}
function search() {
  epoch++;
  clearTimeout(timer);
  loading.value = true;
  timer = setTimeout(() => load(true), 250);
}
function older() {
  if (loading.value) return;
  pages.value[page.value + 1] = next.value;
  page.value++;
  load();
}
function newer() {
  if (loading.value) return;
  page.value--;
  load();
}
async function retry() {
  try {
    await request(endpoint(props.window.id, "retry"), {});
    emit("refresh");
    load(true);
  } catch (e) {
    if (e.name !== "AbortError") error.value = e.message;
  }
}
watch(category, () => load(true));
onMounted(() => {
  load();
  refresh = setInterval(() => {
    if (page.value === 0 && !loading.value && !document.hidden) load();
  }, 5000);
});
onBeforeUnmount(() => {
  epoch++;
  clearTimeout(timer);
  clearInterval(refresh);
});
const stateName = (s) =>
  ({
    baseline: "首次基线",
    pending: "待识别",
    error: "识别失败",
    processed: "已识别",
  })[s] || s;
</script>
<template>
  <section class="panel">
    <div class="section-heading">
      <div>
        <span class="eyebrow">CAPTURED EVIDENCE</span>
        <h2>消息与匹配记录</h2>
      </div>
      <div class="actions">
        <button @click="retry">重试失败记录</button>
        <Hint
          text="将当前窗口失败消息重新排队；开始监控后按当前活动规则识别。已成功记录不会重算。"
        /><button @click="load(true)" :disabled="loading">查看最新</button>
      </div>
    </div>
    <div class="records-filters">
      <input
        v-model="query"
        @input="search"
        type="search"
        placeholder="搜索已保存消息"
        aria-label="搜索已保存消息"
      /><select v-model="category" aria-label="记录类型">
        <option value="all">全部记录</option>
        <option value="matched">匹配提醒</option>
        <option value="error">识别失败</option></select
      ><span class="muted" role="status">{{
        loading ? "正在读取…" : `第 ${page + 1} 页 · ${records.length} 条`
      }}</span>
    </div>
    <p v-if="error" class="error" role="alert">
      {{ error }} · 下方保留上次结果
    </p>
    <div v-if="!records.length && !loading" class="empty-state">
      <span>◎</span>
      <h3>暂无符合条件的消息</h3>
      <p>开始监控并收到新消息后会出现在这里。首次采样默认只建立基线。</p>
    </div>
    <article v-for="record in records" :key="record.id" class="record">
      <div class="record-head">
        <strong>{{ record.payload.sender || "发送者未注明" }}</strong
        ><span class="badge" :class="{ positive: record.result?.matched }">{{
          record.result?.matched ? "匹配目标" : stateName(record.state)
        }}</span
        ><small>{{ timeLabel(record.received_at) }}</small>
      </div>
      <p class="record-text">{{ bodyText(record.payload) }}</p>
      <div v-if="record.result" class="result-reason">
        {{ record.result.reason
        }}<small
          >处理核心：{{ record.result.core }} · 规则
          {{ record.rule_id?.slice(0, 8) }}</small
        >
      </div>
      <p v-if="record.error" class="error">{{ record.error }}</p>
      <details>
        <summary>来源与完整原始记录</summary>
        <pre>{{ JSON.stringify(record.payload, null, 2) }}</pre>
      </details>
    </article>
    <div class="pagination">
      <button @click="newer" :disabled="loading || page === 0">上一页</button
      ><span>第 {{ page + 1 }} 页</span
      ><button @click="older" :disabled="loading || !more">下一页</button>
    </div>
  </section>
</template>
