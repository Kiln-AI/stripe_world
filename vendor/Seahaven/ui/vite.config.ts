import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { viteSingleFile } from "vite-plugin-singlefile"

// The build is one self-contained `dist/index.html`: no sibling assets, no CDN,
// no fetch on load. An environment server mounts that single file at `/console`,
// which puts the page on the same origin as `/ws` and `/schema`.
//
// `vite dev` proxies the same three paths to a local environment so development
// is same-origin too, and the cross-origin degradation path stays a deliberate
// case rather than the default one.
const TARGET = process.env.OPENENV_TARGET ?? "http://127.0.0.1:8000"

export default defineConfig({
  plugins: [react(), tailwindcss(), viteSingleFile()],
  // The dependencies' licence notices travel with the code they cover. esbuild
  // drops every comment by default, which would minify React's `@license`
  // header out of a file that ships inside the wheel, and an attribution
  // licence is complied with by keeping the notice.
  esbuild: { legalComments: "inline" },
  build: {
    target: "es2022",
    assetsInlineLimit: 100 * 1024 * 1024,
    chunkSizeWarningLimit: 4096,
    reportCompressedSize: false,
  },
  server: {
    proxy: {
      "/ws": { target: TARGET, ws: true, changeOrigin: true },
      "/schema": { target: TARGET, changeOrigin: true },
      "/metadata": { target: TARGET, changeOrigin: true },
      "/health": { target: TARGET, changeOrigin: true },
    },
  },
})
