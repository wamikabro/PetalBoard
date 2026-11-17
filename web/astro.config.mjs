// @ts-check
import { defineConfig } from 'astro/config';

// Static app. Pages read ?id= from the URL and load their data from the API in the browser.
// Built as file.html (not file/index.html) so Cloudflare serves /bill directly.
export default defineConfig({
  build: { format: 'file' },
  trailingSlash: 'never',
});
