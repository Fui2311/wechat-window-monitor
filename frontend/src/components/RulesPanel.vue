<script setup>
import { ref, computed, onMounted } from "vue";
import { useRequests } from "../lib/scope";
import { endpoint } from "../lib/api";
import Hint from "./Hint.vue";
const props = defineProps({ window: Object });
const emit = defineEmits(["refresh"]);
const request = useRequests();
const rules = ref([]),
  selected = ref(""),
  editor = ref(""),
  busy = ref(false),
  error = ref(""),
  notice = ref(""),
  testText = ref(""),
  result = ref(null);
const current = computed(() =>
  rules.value.find((x) => x.id === selected.value),
);
const dirty = computed(() => {
  try {
    return (
      JSON.stringify(JSON.parse(editor.value)) !==
      JSON.stringify(current.value?.spec)
    );
  } catch {
    return true;
  }
});
function choose(id) {
  selected.value = id;
  editor.value = JSON.stringify(
    rules.value.find((x) => x.id === id)?.spec || {},
    null,
    2,
  );
  result.value = null;
}
async function load(preferred) {
  const data = await request(endpoint(props.window.id, "rules"));
  rules.value = data.rules;
  choose(preferred || props.window.active_rule || rules.value[0]?.id || "");
}
onMounted(() =>
  load().catch((e) => {
    if (e.name !== "AbortError") error.value = e.message;
  }),
);
async function run(action) {
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    if (action === "generate") {
      const d = await request(endpoint(props.window.id, "generate"), {});
      await load(d.rule.id);
      notice.value = "草稿已保存。核对并试测后，点击启用此版本。";
    } else if (action === "save") {
      const d = await request(endpoint(props.window.id, "rules"), {
        spec: JSON.parse(editor.value),
      });
      await load(d.rule.id);
      notice.value = "编辑已保存为新的规则版本，尚未启用。";
    } else if (action === "activate") {
      await request(endpoint(props.window.id, "activate"), {
        rule_id: selected.value,
      });
      notice.value = "此规则版本已启用，仅影响后续识别。";
      emit("refresh");
    } else if (action === "test") {
      const d = await request(endpoint(props.window.id, "test"), {
        spec: JSON.parse(editor.value),
        message: {
          group: props.window.name,
          text: testText.value,
          sender: "手动试测",
          kind: "text",
        },
      });
      result.value = d.result;
    }
  } catch (e) {
    if (e.name !== "AbortError")
      error.value =
        e instanceof SyntaxError
          ? "规则JSON格式无效，请检查后再保存或测试"
          : e.message;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <section class="panel">
    <div class="section-heading">
      <div>
        <span class="eyebrow">RECOGNITION CORE</span>
        <h2>生成一次，持续使用</h2>
      </div>
      <button class="primary" :disabled="busy" @click="run('generate')">
        {{ busy ? "正在处理…" : "调用模型生成规则" }}
      </button>
    </div>
    <p class="muted">
      {{
        window.mode === "local"
          ? "本地模式：模型仅用于生成规则；启用后由本机执行字符串匹配。"
          : "语义模式：模型先生成判断规则；每条新消息按已保存规则进行模型识别。"
      }}
      <Hint
        text="生成会把识别目标发送给模型；语义试测和识别会发送对应正文。本地试测与日常匹配不联网。"
      />
    </p>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <div v-if="rules.length" class="rule-grid">
      <div>
        <label
          >规则版本<select
            :value="selected"
            @change="choose($event.target.value)"
            :disabled="busy"
          >
            <option v-for="r in rules" :key="r.id" :value="r.id">
              v{{ r.version }} ·
              {{ r.origin === "llm" ? "模型生成" : "手动编辑"
              }}{{ r.id === window.active_rule ? " · 当前启用" : "" }}
            </option>
          </select></label
        ><label
          >规则内容
          <Hint
            text="可直接编辑结构化JSON。本地条件支持 any/all/none 与有限字符串运算，不执行生成代码。编辑后需保存为新版本再启用。"
          /><textarea
            class="code-editor"
            v-model="editor"
            rows="17"
            spellcheck="false"
            :disabled="busy"
            aria-label="规则JSON"
          ></textarea>
        </label>
        <div class="actions">
          <button @click="run('save')" :disabled="busy || !dirty">
            保存为新版本</button
          ><button
            class="primary"
            @click="run('activate')"
            :disabled="
              busy ||
              dirty ||
              window.runtime.running ||
              selected === window.active_rule
            "
          >
            启用此版本
          </button>
        </div>
        <p class="muted" v-if="window.runtime.running">
          更换活动规则前，先在概览停止监控。
        </p>
        <p class="muted" v-if="dirty">当前有未保存编辑，不能直接启用旧版本。</p>
      </div>
      <aside class="test-pane">
        <h3>
          用一条消息试测
          <Hint
            text="只识别此处输入，不写消息库、不生成提醒、不发送邮件。语义模式会调用模型。"
          />
        </h3>
        <textarea
          v-model="testText"
          rows="7"
          placeholder="粘贴一段不含敏感信息的示例消息"
          aria-label="试测消息"
        ></textarea
        ><button @click="run('test')" :disabled="busy || !testText.trim()">
          {{ window.mode === "llm" ? "调用模型试测" : "本地试测" }}
        </button>
        <div
          v-if="result"
          class="test-result"
          :class="{ matched: result.matched }"
          role="status"
        >
          <strong>{{ result.matched ? "匹配目标" : "未匹配" }}</strong>
          <p>{{ result.reason }}</p>
          <blockquote v-if="result.evidence">{{ result.evidence }}</blockquote>
        </div>
      </aside>
    </div>
    <div v-else class="empty-state">
      <span>✧</span>
      <h3>还没有识别规则</h3>
      <p>先在模型设置填写兼容接口与模型，再点击生成。生成结果仅为草稿。</p>
    </div>
  </section>
</template>
