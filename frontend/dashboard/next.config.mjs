/** @type {import('next').NextConfig} */

// Subpath support: on the server the app is served under internet.acuronai.com/admin,
// so set NEXT_PUBLIC_BASE_PATH=/admin at build time. On Mac (dev) leave it empty
// and the app runs at the root (http://localhost:3100).
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

const nextConfig = {
  reactStrictMode: true,
  ...(basePath ? { basePath, assetPrefix: basePath } : {}),
  // Produce a self-contained server bundle for simple systemd/pm2 deploys.
  output: "standalone",
  env: {
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
};

export default nextConfig;
