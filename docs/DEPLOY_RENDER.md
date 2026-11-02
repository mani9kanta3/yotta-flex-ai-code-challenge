# Deploy on Render

The root `render.yaml` creates a public Docker web service and a PostgreSQL database.
The React site and API share one address. Order, transfer, return and exception actions
are enabled, with no login. The hosted demo uses starter data: 300 SKUs and 1,000 orders.

1. Push the deployment files to GitHub.
2. Sign in to Render. Choose **New > Blueprint** and connect this repository.
3. Select `main`, use the root `render.yaml`, and review the two Free plans before creating resources.
4. Wait for the database and web service to finish. Open the service's `onrender.com` URL.
5. Check `/healthz`, `/api/health`, the overview, availability and an order's trace.
   Place an order, confirm it, and refresh to verify it persists.

Render supplies the database settings directly. Do not paste credentials into GitHub.
The first startup seeds an empty application database and computes exceptions in one
transaction. Subsequent starts keep existing data. The initializer holds a database lock
so overlapping starts cannot seed twice. It does not run the resetting `run_all` command.

The image includes the starter evidence files. These describe the generated dataset;
they are not automatically recomputed after visitors place orders.

Free Render services sleep after 15 minutes without traffic, and the free PostgreSQL
database expires after 30 days. Check current limits at https://render.com/docs/free.
Choose a paid plan yourself if the demo needs to stay available longer.

If a release fails, use Render's rollback to the previous working deployment. Startup
does not migrate an existing schema: a future schema change needs a separate migration
and database backup. Do not use the reset/load commands against a live database unless
you intend to remove its visitor-created orders.

Local container check, using a separate database:

```powershell
docker build -t yotta-flex-web .
# Supply DB_HOST, DB_PORT, DB_NAME, DB_USER and DB_PASSWORD for that separate database.
docker run --rm -p 10000:10000 --env-file deployment.env yotta-flex-web
```

Keep `deployment.env` outside the repository. For PostgreSQL on your Windows host,
use `host.docker.internal` as DB_HOST. Never point this first-start test at your working database.

Verified locally on 2 November 2026: the Docker image built from fresh dependency installs;
the site, bundled assets, API, metrics and docs returned HTTP 200; a new order was placed,
confirmed and shipped; restarting the web container kept all 1,001 orders. These checks used
a disposable PostgreSQL container. A live Render deployment is not verified by these checks.
