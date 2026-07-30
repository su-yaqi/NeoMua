import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import { tanstackRouter } from "@tanstack/router-plugin/vite"
import react from "@vitejs/plugin-react-swc"
import { defineConfig } from "vite"

// https://vitejs.dev/config/
export default defineConfig({
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("/node_modules/")) {
            return undefined
          }
          if (
            id.includes("/node_modules/react/") ||
            id.includes("/node_modules/react-dom/") ||
            id.includes("/node_modules/scheduler/")
          ) {
            return "vendor-react"
          }
          if (id.includes("/node_modules/@tanstack/")) {
            return "vendor-tanstack"
          }
          if (
            id.includes("/node_modules/@radix-ui/") ||
            id.includes("/node_modules/lucide-react/") ||
            id.includes("/node_modules/react-icons/")
          ) {
            return "vendor-ui"
          }
          return "vendor"
        },
      },
    },
  },
  server: {
    fs: { allow: [path.resolve(__dirname, "..")] },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  plugins: [
    tanstackRouter({
      target: "react",
      autoCodeSplitting: true,
    }),
    react(),
    tailwindcss(),
  ],
})
