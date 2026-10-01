import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { vendorRoot } from './vendor-path.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
export default defineConfig({
  plugins: [
    {
      name: 'office-only-upstream-transport',
      enforce: 'pre',
      resolveId(source, importer) {
        // Upstream canvas can request terminal focus or seat persistence. This
        // presentation-only entry has neither a terminal nor a Claude server.
        if (importer && importer.replaceAll('\\', '/').includes('/pixel-agents/webview-ui/src/') && /(?:^|\/)transport\/index\.js$/.test(source)) {
          return path.join(here, 'src', 'canvas-transport.ts');
        }
      },
    },
    react(),
  ],
  resolve: {
    alias: [
      { find: '@pixel', replacement: path.join(vendorRoot, 'webview-ui', 'src') },
      { find: /^react-dom(\/.*)?$/, replacement: path.join(here, 'node_modules', 'react-dom') + '$1' },
      { find: /^react(\/.*)?$/, replacement: path.join(here, 'node_modules', 'react') + '$1' },
    ],
    dedupe: ['react', 'react-dom'],
  },
  base: './',
  server: {
    host: '127.0.0.1',
    fs: { allow: [here, vendorRoot] },
    proxy: { '/api/office': process.env.OFFICE_BRIDGE_URL || 'http://127.0.0.1:8790' },
  },
  build: { outDir: 'dist', emptyOutDir: true },
});
