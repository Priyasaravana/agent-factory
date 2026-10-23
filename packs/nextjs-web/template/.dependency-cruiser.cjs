// Module boundaries (ADR-0044 §3, the `modules_isolated` readiness signal): app code reaches a
// module only through its port, `@/modules/<name>`. A module's adapters are private to it, so
// swapping one (AUTH=local → none, METRICS=prometheus → none) never touches app code.
// `make lint` runs this; fix the import, never the rule.
/** @type {import('dependency-cruiser').IConfiguration} */
module.exports = {
  forbidden: [
    {
      name: "adapters-are-private",
      severity: "error",
      comment: "Import the module's port (@/modules/<name>), not its adapters.",
      from: { pathNot: "^src/modules/" },
      to: { path: "^src/modules/[^/]+/adapters/" },
    },
    {
      name: "adapters-are-private-between-modules",
      severity: "error",
      comment: "A module uses another module only through that module's port.",
      from: { path: "^src/modules/([^/]+)/" },
      to: { path: "^src/modules/[^/]+/adapters/", pathNot: "^src/modules/$1/" },
    },
    {
      name: "no-circular",
      severity: "error",
      from: {},
      to: { circular: true },
    },
  ],
  options: {
    doNotFollow: { path: "node_modules" },
    tsPreCompilationDeps: true,
    tsConfig: { fileName: "tsconfig.json" },
    exclude: { path: "^(\\.next|coverage|node_modules)/" },
  },
};
