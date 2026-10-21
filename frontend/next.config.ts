import type { NextConfig } from "next";
const config: NextConfig = {
  poweredByHeader: false,
  experimental: { cpus: 2 },
  async rewrites() {
    return [{ source: "/api/:path*", destination: "http://127.0.0.1:8101/api/:path*" }];
  },
};
export default config;
