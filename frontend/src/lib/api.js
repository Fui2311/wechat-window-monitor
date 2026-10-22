export function createApi(transport = fetch) {
  return async function api(path, body, signal) {
    const response = await transport(path, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error("服务响应无效，请检查控制台进程");
    }
    if (!response.ok || data.ok === false)
      throw new Error(data.error || `请求失败 (${response.status})`);
    return data;
  };
}
export const api = createApi();
export const endpoint = (id, action = "") =>
  `/api/windows/${encodeURIComponent(id)}${action ? "/" + action : ""}`;
