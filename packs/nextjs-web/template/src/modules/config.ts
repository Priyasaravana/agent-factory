// Which adapter each module runs: the one place it is decided (ADR-0044 §3). App code imports
// only `@/modules/<name>`; it never knows which adapter is behind the port.
//
// DEFAULTS follow the product's design decisions (docs/decisions.yaml): set `auth` to "local"
// when `authentication` is local, and `authz` to "signed-in" or "roles" to match `authorisation`.
// The environment can override each one (AUTH, AUTHZ, LOGGING, METRICS, TRACING), for example
// LOGGING=silent in a test.

export const ADAPTERS = {
  auth: ["none", "local"],
  authz: ["allow-all", "signed-in", "roles"],
  logging: ["pino", "silent"],
  metrics: ["prometheus", "none"],
  tracing: ["otel", "off"],
} as const;

type Adapters = typeof ADAPTERS;
export type ModuleName = keyof Adapters;
export type ModuleConfig = { [K in ModuleName]: Adapters[K][number] };

export const DEFAULTS: Omit<ModuleConfig, "tracing"> = {
  auth: "none",
  authz: "allow-all",
  logging: "pino",
  metrics: "prometheus",
};

type Env = Record<string, string | undefined>;

function pick<K extends ModuleName>(name: K, value: string | undefined): ModuleConfig[K] {
  const allowed: readonly string[] = ADAPTERS[name];
  if (!value) throw new Error(`modules: no adapter for ${name}`);
  if (!allowed.includes(value)) {
    throw new Error(`modules: ${name}=${value} is not an adapter (one of ${allowed.join(", ")})`);
  }
  return value as ModuleConfig[K];
}

/** The adapters for this process: DEFAULTS, then the environment. Throws on an unknown adapter or
 * a combination that can't work, so a misconfigured app fails at start, not on a request. */
export function moduleConfig(env: Env = process.env): ModuleConfig {
  const cfg: ModuleConfig = {
    auth: pick("auth", env.AUTH || DEFAULTS.auth),
    authz: pick("authz", env.AUTHZ || DEFAULTS.authz),
    logging: pick("logging", env.LOGGING || DEFAULTS.logging),
    metrics: pick("metrics", env.METRICS || DEFAULTS.metrics),
    // spans need somewhere to go: on only when a collector is configured
    tracing: pick("tracing", env.TRACING || (env.OTEL_EXPORTER_OTLP_ENDPOINT ? "otel" : "off")),
  };
  if (cfg.auth === "none" && cfg.authz !== "allow-all") {
    throw new Error(`modules: authz=${cfg.authz} needs a way to sign in (auth=local)`);
  }
  return cfg;
}
