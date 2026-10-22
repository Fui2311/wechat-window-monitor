<script setup>
import { ref, onMounted, onBeforeUnmount } from "vue";
import { statusView } from "../lib/presentation";
defineProps({ windows: Array, selected: Object });
const emit = defineEmits(["select", "add"]);
const open = ref(false),
  root = ref(null),
  trigger = ref(null);
function close() {
  open.value = false;
  trigger.value?.focus();
}
function outside(e) {
  if (root.value && !root.value.contains(e.target)) open.value = false;
}
onMounted(() => document.addEventListener("pointerdown", outside));
onBeforeUnmount(() => document.removeEventListener("pointerdown", outside));
function choose(id) {
  emit("select", id);
  open.value = false;
  trigger.value?.focus();
}
</script>
<template>
  <div class="window-menu" ref="root" @keydown.esc="close">
    <button
      class="window-trigger"
      ref="trigger"
      @click="open = !open"
      :aria-expanded="open"
      aria-controls="window-list"
    >
      <span class="app-mark">W</span
      ><span class="window-trigger-text"
        ><small>WINDOW MONITOR</small
        ><strong
          ><i class="dot" :class="statusView(selected?.runtime).color"></i
          >{{ selected?.name || "选择监控窗口" }}</strong
        ></span
      ><span class="chevron">⌄</span>
    </button>
    <div v-if="open" id="window-list" class="window-popover">
      <div class="menu-label">我的微信窗口</div>
      <button
        v-for="w in windows"
        :key="w.id"
        class="window-option"
        :class="{ chosen: w.id === selected?.id }"
        @click="choose(w.id)"
      >
        <i class="dot" :class="statusView(w.runtime).color"></i
        ><span
          >{{ w.name }}<small>{{ statusView(w.runtime).label }}</small></span
        ><span v-if="w.id === selected?.id">✓</span></button
      ><button
        class="add-window-option"
        @click="
          open = false;
          emit('add');
        "
      >
        <b>⊕</b> 添加窗口监控
      </button>
    </div>
  </div>
</template>
