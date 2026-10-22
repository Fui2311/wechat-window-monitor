<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { api } from "./lib/api";
import { statusView } from "./lib/presentation";
import WindowMenu from "./components/WindowMenu.vue";
import AddWindowDialog from "./components/AddWindowDialog.vue";
import OverviewPanel from "./components/OverviewPanel.vue";
import RulesPanel from "./components/RulesPanel.vue";
import RecordsPanel from "./components/RecordsPanel.vue";
import WindowSettings from "./components/WindowSettings.vue";
import ModelSettings from "./components/ModelSettings.vue";
const defaultCapture = ref(null);
const windows = ref([]),
  model = ref(null),
  selectedId = ref(
    location.hash.match(/^#\/windows\/([a-f0-9]{32})\//)?.[1] ||
      localStorage.getItem("monitor.selected") ||
      "",
  ),
  tab = ref(
    ["overview", "records", "rules", "settings", "model"].includes(
      location.hash.split("/")[3],
    )
      ? location.hash.split("/")[3]
      : "overview",
  ),
  adding = ref(false),
  error = ref(""),
  version = ref(""),
  online = ref(true);
let timer,
  epoch = 0;
const selected = computed(() =>
  windows.value.find((w) => w.id === selectedId.value),
);
const tabs = [
  ["overview", "概览"],
  ["records", "消息记录"],
  ["rules", "识别规则"],
  ["settings", "窗口设置"],
  ["model", "模型设置"],
];
async function refresh() {
  const request = ++epoch;
  try {
    const data = await api("/api/bootstrap");
    if (request !== epoch) return;
    windows.value = data.windows;
    model.value = data.model;
    version.value = data.version;
    defaultCapture.value = data.default_capture;
    online.value = true;
    error.value = "";
    if (!selected.value && windows.value.length) select(windows.value[0].id);
  } catch (e) {
    if (request === epoch) {
      online.value = false;
      error.value = e.message;
      windows.value = windows.value.map((w) => ({
        ...w,
        runtime: { ...w.runtime, unavailable: true },
      }));
    }
  }
}
function select(id) {
  selectedId.value = id;
  localStorage.setItem("monitor.selected", id);
  tab.value = "overview";
  history.replaceState(null, "", `#/windows/${id}/overview`);
}
function setTab(value) {
  tab.value = value;
  history.replaceState(null, "", `#/windows/${selectedId.value}/${value}`);
}
async function created(window) {
  await refresh();
  select(window.id);
  setTab("settings");
  adding.value = false;
}
onMounted(() => {
  refresh();
  timer = setInterval(() => {
    if (!document.hidden) refresh();
  }, 4000);
});
onBeforeUnmount(() => clearInterval(timer));
</script>
<template>
  <div class="app-shell">
    <aside class="sidebar">
      <WindowMenu
        :windows="windows"
        :selected="selected"
        @select="select"
        @add="adding = true"
      />
      <div class="sidebar-section">工作空间</div>
      <nav aria-label="窗口页面">
        <button
          v-for="[key, label] in tabs"
          :key="key"
          :class="{ active: tab === key }"
          @click="setTab(key)"
          :disabled="!selected && key !== 'model'"
        >
          <span class="nav-icon">{{
            {
              overview: "◫",
              records: "▤",
              rules: "◇",
              settings: "⚙",
              model: "✧",
            }[key]
          }}</span
          >{{ label
          }}<span
            v-if="key === 'records' && selected?.counts.matched"
            class="nav-count"
            >{{ selected.counts.matched }}</span
          >
        </button>
      </nav>
      <div class="sidebar-footer">
        <i class="dot" :class="online ? 'green' : 'gray'"></i
        >{{ online ? "本地控制台已连接" : "连接中断"
        }}<small>v{{ version || "—" }} · 仅本机访问</small>
      </div>
    </aside>
    <main>
      <header class="topbar">
        <div class="breadcrumb">
          工作空间 <span>/</span> {{ selected?.name || "开始使用" }}
        </div>
        <div class="header-actions">
          <span v-if="selected" class="status-pill"
            ><i
              class="dot"
              :class="online ? statusView(selected.runtime).color : 'gray'"
            ></i
            >{{
              online ? statusView(selected.runtime).label : "状态不可用"
            }}</span
          ><button
            class="icon-button"
            @click="refresh"
            aria-label="刷新所有窗口"
          >
            ↻
          </button>
        </div>
      </header>
      <div class="content">
        <div class="page-heading">
          <div>
            <span class="eyebrow">WECHAT WINDOW MONITOR</span>
            <h1>{{ tabs.find((x) => x[0] === tab)?.[1] }}</h1>
            <p>把注意力留给真正重要的消息。</p>
          </div>
          <span class="workspace-badge">本地工作空间</span>
        </div>
        <p v-if="error" class="error" role="alert">
          {{ error }} · 状态可能已过期
        </p>
        <ModelSettings
          v-if="tab === 'model' && model"
          :model="model"
          @refresh="refresh"
        /><template v-else-if="selected"
          ><OverviewPanel
            v-if="tab === 'overview'"
            :key="selected.id"
            :window="selected"
            @refresh="refresh"
            @tab="setTab" /><RecordsPanel
            v-else-if="tab === 'records'"
            :key="selected.id"
            :window="selected"
            @refresh="refresh" /><RulesPanel
            v-else-if="tab === 'rules'"
            :key="selected.id"
            :window="selected"
            @refresh="refresh" /><WindowSettings
            v-else-if="tab === 'settings'"
            :key="selected.id"
            :window="selected"
            @refresh="refresh"
        /></template>
        <section v-else class="panel welcome">
          <div class="welcome-mark">⊕</div>
          <span class="eyebrow">YOUR OWN SIGNAL</span>
          <h2>从一个微信窗口开始</h2>
          <p>
            精确填写会话名称，定义你关注的内容。<br />生成可复用的规则，让消息按你的目标归类。
          </p>
          <button class="primary" @click="adding = true">
            ⊕ 添加第一个窗口
          </button>
          <p class="muted">
            群聊、私聊与其他可读会话 · 两种识别核心 · 原文始终保留
          </p>
        </section>
        <footer class="content-footer">
          精确名称绑定 · 仅已加载的可读消息 · 本地规则日常识别不联网
        </footer>
      </div>
    </main>
    <AddWindowDialog
      :default-capture="defaultCapture"
      v-if="adding"
      @close="adding = false"
      @created="created"
    />
  </div>
</template>
