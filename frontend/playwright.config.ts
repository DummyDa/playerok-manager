import { defineConfig } from "@playwright/test";
const port = Number(process.env.PLAYWRIGHT_PORT || "3100");
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("Invalid Playwright port");
export default defineConfig({
  testDir: "./tests", fullyParallel: false, workers: 1,
  use: { baseURL: `http://127.0.0.1:${port}`, headless: true,
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {} },
  reporter: "list",
  webServer: { command: `npx next start --hostname 127.0.0.1 --port ${port}`, url: `http://127.0.0.1:${port}`, reuseExistingServer: !process.env.CI, timeout: 30000 },
});
