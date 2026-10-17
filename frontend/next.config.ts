import type { NextConfig } from "next";

// Production: a static export in out/ that FastAPI serves on the API's own origin.
// Development: next dev proxies /api to the FastAPI backend so the session cookie stays same-origin.
const backend = process.env.STEP_WISE_BACKEND ?? process.env.SAFE_HOME_BACKEND ?? "http://127.0.0.1:8010";
const production = process.env.NODE_ENV === "production";

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
  ...(production
    ? { output: "export" }
    : {
        async rewrites() {
          return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
        },
      }),
};

export default nextConfig;
