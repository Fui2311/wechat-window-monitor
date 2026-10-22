import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { developmentHeaders } from "./src/lib/devProxy.js";
const target = "http://127.0.0.1:8765";
export default defineConfig({
  plugins: [vue()],
  build: { outDir: "../wechat_monitor/resources/dashboard", emptyOutDir: true },
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api": {
        target,
        changeOrigin: true,
        configure(proxy) {
          proxy.on("proxyReq", (outgoing, request) => {
            for (const [key, value] of Object.entries(
              developmentHeaders(request.headers, target),
            ))
              outgoing.setHeader(key, value);
          });
        },
      },
    },
  },
});
