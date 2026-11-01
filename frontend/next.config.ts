import type { NextConfig } from "next";
const config: NextConfig = {
  poweredByHeader: false,
  // Avoid reusing upstream sockets closed by the local API during idle periods/restarts.
  httpAgentOptions: { keepAlive: false },
  experimental: { cpus: 2 },
  async headers() {
    return [{ source: "/:path*", headers: [
      { key: "X-Frame-Options", value: "DENY" },
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "Referrer-Policy", value: "no-referrer" },
      { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
      { key: "Content-Security-Policy", value: "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'" },
    ] }];
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: "http://127.0.0.1:8101/api/:path*" }];
  },
};
export default config;
