import type { NextConfig } from "next";

const config: NextConfig = {
  // one self-contained server in the image (Dockerfile copies .next/standalone)
  output: "standalone",
  poweredByHeader: false,
  // server-only packages: kept out of the bundle, traced into the standalone output
  serverExternalPackages: ["pg", "pino", "@prometheus-io/client"],
};

export default config;
