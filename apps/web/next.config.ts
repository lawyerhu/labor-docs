import type { NextConfig } from "next";

// Local development mirrors production: web -> local Cloudflare Worker
// (wrangler dev on 8787) -> local generator on 8000. Deployment platforms
// provide their API endpoint explicitly through API_INTERNAL_URL.
const apiBase = process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8787";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiBase}/api/:path*` }];
  },
};

export default nextConfig;
