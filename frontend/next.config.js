/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
  },
  async redirects() {
    return [
      // Old home -> Dashboard
      { source: "/home", destination: "/dashboard", permanent: true },
      // Root -> Dashboard
      { source: "/", destination: "/dashboard", permanent: false },
      // Compute pipelines -> Pipelines
      { source: "/compute/pipelines/catalog", destination: "/pipelines/catalog", permanent: true },
      { source: "/compute/pipelines/runs", destination: "/pipelines/runs", permanent: true },
      // Compute notebooks -> Notebooks
      { source: "/compute/notebooks", destination: "/notebooks", permanent: true },
      // Compute -> Infrastructure compute
      { source: "/compute", destination: "/infrastructure/compute", permanent: true },
      { source: "/compute/cluster", destination: "/infrastructure/compute", permanent: true },
      { source: "/compute/jobs", destination: "/infrastructure/compute", permanent: true },
      { source: "/compute/quotas", destination: "/infrastructure/compute", permanent: true },
      // Environment -> Infrastructure
      { source: "/environment/components", destination: "/infrastructure/components", permanent: true },
      { source: "/components", destination: "/infrastructure/components", permanent: true },
      { source: "/components/:id", destination: "/infrastructure/components/:id", permanent: true },
      { source: "/environment/history", destination: "/environments", permanent: true },
      { source: "/environment/packages", destination: "/environments", permanent: true },
      // Admin -> Settings and Infrastructure
      { source: "/admin/cost-center", destination: "/infrastructure/cost-center", permanent: true },
      { source: "/admin/costs", destination: "/infrastructure/cost-center", permanent: true },
      { source: "/admin/backup", destination: "/infrastructure/backup", permanent: true },
      { source: "/admin/backups", destination: "/infrastructure/backup", permanent: true },
      { source: "/admin/users", destination: "/settings/users", permanent: true },
      { source: "/admin/access-logs", destination: "/settings/audit-log", permanent: true },
      { source: "/admin/settings", destination: "/settings/info", permanent: true },
      { source: "/admin/naming-profiles", destination: "/settings/naming-profiles", permanent: true },
      { source: "/admin/templates", destination: "/projects/experiment-templates", permanent: true },
      // Old experiment templates path
      { source: "/notebooks/templates", destination: "/projects/experiment-templates", permanent: true },
      // Experiment routes moved under /projects/
      { source: "/experiments/templates", destination: "/projects/experiment-templates", permanent: true },
      { source: "/experiments/:id", destination: "/projects/experiments/:id", permanent: true },
      { source: "/experiments", destination: "/projects/experiments", permanent: true },
      // Old pipeline paths
      { source: "/pipelines", destination: "/pipelines/catalog", permanent: true },
      // Old data paths
      { source: "/data", destination: "/data/browser", permanent: true },
      // Old references path
      { source: "/references", destination: "/data/references", permanent: true },
      { source: "/references/:id", destination: "/data/references/:id", permanent: true },
      // Old results path
      { source: "/results", destination: "/results/qc-dashboards", permanent: true },
      // Ingest -> Data upload
      { source: "/ingest", destination: "/data/upload", permanent: true },
    ];
  },
};

module.exports = nextConfig;
