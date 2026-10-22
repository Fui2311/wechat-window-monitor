import { test } from "node:test";
import assert from "node:assert/strict";
import { createApi, endpoint } from "../src/lib/api.js";
import { statusView, bodyText } from "../src/lib/presentation.js";
test("actual HTTP adapter parses failures and never claims success on non-JSON", async () => {
  const api = createApi(async (path, options) => {
    assert.equal(path, endpoint("a", "generate"));
    assert.equal(options.method, "POST");
    assert.equal(options.body, "{}");
    return {
      ok: false,
      status: 400,
      json: async () => ({ ok: false, error: "模型未配置" }),
    };
  });
  await assert.rejects(() => api("/api/windows/a/generate", {}), /模型未配置/);
  await assert.rejects(
    () =>
      createApi(async () => ({
        ok: true,
        json: async () => {
          throw new Error();
        },
      }))("/api/bootstrap"),
    /响应无效/,
  );
});
test("only live successfully capturing monitors have green markers", () => {
  assert.equal(
    statusView({ running: true, status: "monitoring" }).color,
    "green",
  );
  for (const status of ["stopped", "stopping", "starting"])
    assert.equal(statusView({ running: true, status }).color, "gray");
  assert.equal(
    statusView({ running: false, status: "monitoring" }).color,
    "gray",
  );
  assert.equal(statusView({ running: true, status: "waiting" }).color, "amber");
  assert.deepEqual(
    statusView({ running: true, status: "monitoring", unavailable: true }),
    { label: "状态不可用", color: "gray" },
  );
});
test("visible original text includes captured nested bodies and attachment names", () => {
  assert.equal(
    bodyText({
      text: "摘要",
      children: [{ text: "已读取的正文" }],
      attachments: [{ name: "附件.pdf" }],
    }),
    "摘要\n已读取的正文\n附件.pdf",
  );
});

test("development proxy only rewrites matching localhost origin and referer", async () => {
  const { developmentHeaders } = await import("../src/lib/devProxy.js");
  assert.deepEqual(
    developmentHeaders(
      {
        host: "127.0.0.1:5173",
        origin: "http://127.0.0.1:5173",
        referer: "http://127.0.0.1:5173/page",
      },
      "http://127.0.0.1:8765",
    ),
    { origin: "http://127.0.0.1:8765", referer: "http://127.0.0.1:8765" },
  );
  for (const origin of [
    "https://evil.example",
    "http://127.0.0.1:9999",
    "http://evil@127.0.0.1:5173",
    "malformed",
  ])
    assert.deepEqual(
      developmentHeaders(
        { host: "127.0.0.1:5173", origin },
        "http://127.0.0.1:8765",
      ),
      {},
    );
});
