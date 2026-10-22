<script setup>
import { useId } from "vue";
defineProps({ text: String });
const hintId = useId();
</script>
<template>
  <span class="hint-wrap"
    ><span
      tabindex="0"
      class="hint"
      role="note"
      :aria-label="text"
      :aria-describedby="hintId"
      @click.stop
      @keydown.enter.stop.prevent
      >?</span
    ><span role="tooltip" class="tooltip" :id="hintId">{{ text }}</span></span
  >
</template>
