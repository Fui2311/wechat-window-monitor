/** HTTP adapter with an injected fetch implementation and no application state. */
const TrackerHttp = (() => {
  'use strict';
  function createApi(fetchResponse) {
    async function request(endpoint, options) {
      const response = await fetchResponse(endpoint, options);
      if (!response.ok) {
        const text = await response.text();
        let message = text || `HTTP ${response.status}`;
        try {
          const data = JSON.parse(text);
          if (data && data.error) message = data.error;
        } catch (error) {}
        throw new Error(message);
      }
      return response;
    }
    return Object.freeze({
      async get(endpoint) { return (await request(endpoint)).json(); },
      async post(endpoint, body = {}) {
        return (await request(endpoint, {method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify(body)})).json();
      },
      async text(endpoint) { return (await request(endpoint)).text(); },
    });
  }
  return Object.freeze({createApi});
})();

if (typeof module !== 'undefined' && module.exports) module.exports = TrackerHttp;
