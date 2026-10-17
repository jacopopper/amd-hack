import type { NextConfig } from "next";

// Dev proxy to the FastAPI backend so the session cookie stays same-origin.
const backend = process.env.SAFE_HOME_BACKEND ?? "http://127.0.0.1:8010";

const nextConfig: NextConfig = {
  devIndicators: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
