import type { NextConfig } from "next";

const apiBase = process.env.API_INTERNAL_URL ?? "https://labor-docs-api.fayan-research.workers.dev";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiBase}/api/:path*` }];
  },
};

export default nextConfig;
