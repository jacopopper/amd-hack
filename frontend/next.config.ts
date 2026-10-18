import type { NextConfig } from "next";

// Production: a static export in out/ that FastAPI serves on the API's own origin.
// Development: next dev proxies /api to the FastAPI backend on the same origin.
const backend = process.env.STEP_WISE_BACKEND ?? process.env.SAFE_HOME_BACKEND ?? "http://127.0.0.1:8010";
const production = process.env.NODE_ENV === "production";

const nextConfig: NextConfig = {
  devIndicators: false,
  ...(process.env.NEXT_PUBLIC_STATIC_DEMO === "true" ? { trailingSlash: true, distDir: ".next-demo" } : {}),
  ...(production
    ? { output: "export" }
    : {
        async rewrites() {
          return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
        },
      }),
};

export default nextConfig;
