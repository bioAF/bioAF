# ADR-027: Navigation Restructure

**Status:** Accepted
**Date:** 2026-03-10
**Deciders:** Brent (repository owner)

---

## Context

bioAF's original navigation structure was organized around the platform's eight architectural layers: Setup/Provisioning, Experiment Tracking, Data Management, Compute, Pipeline Orchestration, Interactive Analysis, Visualization, and Package Management/GitOps. This structure mirrors how the platform was built, not how users think about their work.

In practice, this architecture-oriented navigation leads to:

- Users jumping between multiple top-level sections to complete a single workflow
- Related pages (e.g., pipeline runs and their QC dashboard results) living in different sections
- Difficulty finding pages: features like analysis snapshots and GEO export are buried multiple clicks deep with no clear path
- Multiple routes to the same page that may render differently depending on the navigation path taken
- The navigation menu expanding to show too many items at once, creating cognitive overload

The restructure reorients navigation around user workflows rather than platform architecture. No pages, data models, or permissions change: this is purely a presentation reorganization.

---

## Decision

Replace the architecture-oriented navigation with a workflow-oriented hierarchy. The top-level navigation items represent what users are trying to do, not what layer of the platform they are interacting with.

### Navigation Hierarchy

```text
Dashboard
Results
  ├── QC Dashboards
  ├── Cellxgene
  └── Plot Archive
Pipelines
  ├── Pipeline Catalog
  ├── Pipeline Runs
  └── Pipeline Scheduling
Projects
  ├── Project List
  ├── Experiment Templates
  └── Experiment List
Notebooks
Data & Files
  ├── Upload
  ├── Dataset Browser
  ├── Documents
  └── Reference Data
Infrastructure
  ├── Components
  ├── Compute
  ├── Environments
  ├── Packages
  ├── Cost Center
  └── Backup & Recovery
Settings (admin only)
  ├── Users & Roles
  ├── Access Logs
  ├── SMTP Configuration
  ├── Slack Integration
  └── Information
```

### Interaction Model

**Top-level items** are always visible in the left navigation sidebar. Items with sub-pages show an expand/collapse chevron. Clicking the chevron (or the top-level item) expands to reveal the secondary navigation items. Clicking a secondary item loads that page.

**Single-page items** (Dashboard, Notebooks) load directly when clicked: no expand/collapse, no secondary items.

**Tertiary navigation** does not exist in the sidebar. Deeper navigation happens within pages through tabs and contextual links. For example:

- Projects page -> click a project -> Project Detail page with tabs (Experiments, Samples, Data, Analysis, Provenance)
- Experiments -> Experiment List -> click an experiment -> Experiment Detail page with tabs (Samples, Data, Pipeline Runs, Analysis, Audit Trail)

**Breadcrumbs** at the top of every page show the full navigation path. The left sidebar highlights the active top-level and secondary item.

### Page-to-Section Mapping

| Page | Old Location | New Location |
| --- | --- | --- |
| Home dashboard | Home | Dashboard (top-level) |
| QC Dashboards | Results → QC Dashboards | Results → QC Dashboards |
| cellxgene | Results → cellxgene | Results → Cellxgene |
| Plot Archive | Results → Plot Archive | Results → Plot Archive |
| Pipeline Catalog | Compute → Pipelines → Catalog | Pipelines → Pipeline Catalog |
| Pipeline Runs (active + history) | Compute → Pipelines → Active Runs / Run History | Pipelines → Pipeline Runs |
| Pipeline Scheduling | (new page) | Pipelines → Pipeline Scheduling |
| Project List | (was organizational only) | Projects → Project List |
| Experiment Templates | Admin → Experiment Templates | Projects → Experiment Templates |
| Experiment List | Experiments → All Experiments | Projects → Experiment List |
| Notebook launcher + templates | Compute → Notebooks | Notebooks (top-level) |
| FASTQ Upload | Data → Upload | Data & Files → Upload |
| Dataset Browser | Data → Browser | Data & Files → Dataset Browser |
| Document Library | Data → Documents | Data & Files → Documents |
| Reference Data | Data → (was not its own nav item) | Data & Files → Reference Data |
| Component Catalog | Environment → Components | Infrastructure → Components |
| Cluster Status / Job Browser / Quotas | Compute → Cluster Status | Infrastructure → Compute |
| Environment History | Environment → Environment History | Infrastructure → Environments |
| Package Browser | Environment → Packages | Infrastructure → Packages |
| Cost Center | Admin → Cost Center | Infrastructure → Cost Center |
| Backup & Recovery | Admin → Backup & Recovery | Infrastructure → Backup & Recovery |
| Users & Roles | Admin → Users + Roles | Settings → Users & Roles |
| Access Logs | Admin → Access Logs | Settings → Access Logs |
| SMTP Configuration | Admin → Notifications (partial) | Settings → SMTP Configuration |
| Slack Integration | Admin → Notifications (partial) | Settings → Slack Integration |
| Platform Info / Version | Admin → bioAF Settings | Settings → Information |

### Dashboard Content

The Dashboard is a single page with summarized widgets:

- **Infrastructure health:** Component status indicators (healthy/degraded/down)
- **Running jobs:** Count of active pipeline runs with status summary
- **Queue depth:** Jobs waiting to execute
- **Cost vs. budget:** Current month spend against configured budget
- **Activity feed:** Truncated recent activity with an expand button that opens the full Activity Feed page. Individual items are clickable for detail.

Future: per-user dashboard customization (add/remove widgets) is deferred to a later phase.

### Contextual Data Access

The same data is accessible through multiple navigation paths, but the context determines the filter:

- **Samples via Experiments:** Experiments → Experiment List → Experiment 123 → Samples tab. Shows only samples belonging to Experiment 123.
- **Samples via Projects:** Projects → Project ABC → Samples tab. Shows all samples across all experiments in Project ABC.
- **Samples via Dataset Browser:** Data & Files → Dataset Browser. Shows all samples across the organization with full filter controls.

In each case, the left sidebar highlights which section the user is in, and breadcrumbs show the full path. This makes the filtering context explicit: "I see these samples because I'm looking at Experiment 123" vs. "I see all samples because I'm in the Dataset Browser."

### What Does Not Change

- **No page content changes.** Every existing page renders the same content with the same layout.
- **No permission changes.** Role-based visibility remains as-is.
- **No data model changes.** No schema migrations, no API changes.
- **URL restructuring for experiment routes.** Old `/experiments`, `/experiments/:id`, and `/experiments/templates` URLs redirect permanently (HTTP 301) to their new paths under `/projects/`. All other API routes and page URLs remain stable.

---

## Consequences

**Positive:**

- Navigation reflects user workflows, not platform architecture
- Fewer clicks to reach commonly used pages
- Clear contextual data access: users understand why they see filtered vs. unfiltered data
- The collapsible sidebar keeps the nav manageable even with many sections
- New features (Pipeline Scheduling, Reference Data) have clear homes in the hierarchy

**Negative:**

- Existing users need to relearn where pages are: muscle memory from the old nav breaks
- Some pages have arguable placement (e.g., Compute under Infrastructure vs. under Pipelines): any hierarchy is a compromise
- The sidebar is still relatively long (9 top-level items); on small screens it may require scrolling

**Mitigations:**

- The restructure happens alongside other significant feature additions (auto-ingest, pipeline triggers, K8s migration), so users are already adapting to new workflows
- The breadcrumb trail provides orientation for users who land on a page via search or direct link

---

## References

- Product Spec v0.4, Section 6.2 (original navigation structure)
- ADR-025 (automated pipeline triggering: Pipeline Scheduling page)
- ADR-026 (SSH access: Connect button placement in Infrastructure → Compute)
