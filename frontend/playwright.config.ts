import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests", fullyParallel: false, workers: 1,
  use: { baseURL: "http://127.0.0.1:3100", headless: true,
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {} },
  reporter: "list",
  webServer: { command: "npm run start", url: "http://127.0.0.1:3100", reuseExistingServer: true, timeout: 30000 },
});
