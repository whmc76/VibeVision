import react from "@vitejs/plugin-react";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const currentDir = dirname(fileURLToPath(import.meta.url));
const rootDir = resolve(currentDir, "..");
const configPaths = [
  resolve(rootDir, "config", "vibevision.env"),
  resolve(rootDir, "config", "vibevision.local.env"),
];

function readUnifiedConfig(): Record<string, string> {
  const config: Record<string, string> = {};

  for (const configPath of configPaths) {
    if (!existsSync(configPath)) continue;

    const content = readFileSync(configPath, "utf-8");
    for (const line of content.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;

      const separator = trimmed.indexOf("=");
      if (separator === -1) continue;

      const key = trimmed.slice(0, separator).trim();
      const value = trimmed.slice(separator + 1).trim();
      config[key] = value;
      process.env[key] = value;
    }
  }

  process.env.VITE_API_BASE_URL ??=
    config.VITE_API_BASE_URL ?? `http://localhost:${config.API_PORT ?? "18751"}`;

  return config;
}

export default defineConfig(() => {
  const config = readUnifiedConfig();
  const host = config.ADMIN_FRONTEND_HOST ?? "127.0.0.1";
  const port = Number(config.ADMIN_FRONTEND_PORT ?? 18742);

  return {
    plugins: [react()],
    server: {
      host,
      port,
      strictPort: true,
    },
    preview: {
      host,
      port,
      strictPort: true,
    },
  };
});
