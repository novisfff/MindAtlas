import path from "path"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

function matchesNodeModulePackage(id: string, packageName: string): boolean {
  return id.includes(`/node_modules/${packageName}/`) || id.endsWith(`/node_modules/${packageName}`)
}

function matchesAnyPackage(id: string, packageNames: string[]): boolean {
  return packageNames.some((packageName) => matchesNodeModulePackage(id, packageName))
}

export default defineConfig({
  plugins: [react()],
  optimizeDeps: {
    exclude: ["@wasm-fmt/ruff_fmt"],
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) {
            return undefined
          }
          if (matchesAnyPackage(id, ["@xyflow/react"])) {
            return "workflow-xyflow"
          }
          if (matchesAnyPackage(id, ["@uiw/react-codemirror", "codemirror"])) {
            return "workflow-codemirror-core"
          }
          if (matchesAnyPackage(id, ["@codemirror/lang-javascript", "@lezer/javascript"])) {
            return "workflow-codemirror-javascript"
          }
          if (matchesAnyPackage(id, ["@codemirror/lang-python", "@lezer/python"])) {
            return "workflow-codemirror-python"
          }
          if (id.includes("/node_modules/@codemirror/") || id.includes("/node_modules/@lezer/")) {
            return "workflow-codemirror-langs"
          }
          if (matchesAnyPackage(id, ["@wasm-fmt/ruff_fmt"])) {
            return "workflow-ruff"
          }
          if (matchesAnyPackage(id, ["react-dom", "react-router-dom", "react", "scheduler"])) {
            return "vendor-react"
          }
          if (matchesAnyPackage(id, ["@tanstack/react-query", "i18next", "react-i18next", "zustand"])) {
            return "vendor-state"
          }
          if (matchesAnyPackage(id, [
            "@radix-ui",
            "@dnd-kit",
            "lucide-react",
            "sonner",
            "class-variance-authority",
            "clsx",
            "tailwind-merge",
          ])) {
            return "vendor-ui"
          }
          if (matchesAnyPackage(id, ["date-fns", "react-day-picker", "react-activity-calendar"])) {
            return "vendor-date"
          }
          return undefined
        },
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 3000,
    proxy: {
      "/api": "http://localhost:8000"  // Python FastAPI backend
    }
  },
})
