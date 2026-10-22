import { onBeforeUnmount } from "vue";
import { api } from "./api";
export function useRequests() {
  const controller = new AbortController();
  onBeforeUnmount(() => controller.abort());
  return (path, body) => api(path, body, controller.signal);
}
