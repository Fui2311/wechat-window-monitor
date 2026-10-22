// Only translate same-origin localhost development headers, preserving rejection of external origins.
export function developmentHeaders(headers, target) {
  const result = {};
  for (const field of ["origin", "referer"]) {
    const raw = headers[field];
    if (!raw) continue;
    try {
      const url = new URL(raw);
      if (
        url.protocol === "http:" &&
        ["localhost", "127.0.0.1"].includes(url.hostname) &&
        url.host === headers.host &&
        !url.username &&
        !url.password
      )
        result[field] = target;
    } catch {
      /* Invalid or external headers remain unchanged and backend rejects them. */
    }
  }
  return result;
}
