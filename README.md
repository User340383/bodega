# Bodega

A web app for small shops to capture sales from a photo or text, confirm them, and get restocking estimates. Built for COMP 425, Team Bodega. Each member's report lists the details of their part:

- [Luis Heyaime](docs/LUIS_IMPLEMENTATION.md): application shell, Docker, CI/CD, Render deployment
- [José Javier Otazu](docs/JOSE_IMPLEMENTATION.md): AI ingestion and capture
- [Diushen Cabrera](docs/DIUSHEN_IMPLEMENTATION.md): analysis, guidance and results dashboard
- [Gabriel Marte](docs/GABRIEL_IMPLEMENTATION.md): data and state, draft review, sales history

The app's pages are `/login`, `/capture`, `/review/:submissionId`, `/drafts`, `/history` and `/analysis`. Production runs on Render (see `render.yaml` and the deployment section of Luis's report); code reaches production only through the GitHub Actions deploy job after every check passes on `main`.

## Start locally

Requires Docker Desktop with Linux containers and Docker Compose.

```sh
docker compose up --build -d --wait
```

Open http://localhost:5173. The development owner access code is `local-development-code-123`, matching the existing Compose configuration. API documentation is at http://localhost:8000/docs. Schema migrations run automatically during backend startup.

To capture sales, use **Capture sales** at the top of the page: choose **Text** and paste lines such as `Bread 3 1.50`, or upload a JPEG/PNG photo. With the default `AI_PROVIDER=mock` no paid key is needed; see [the ingestion report](docs/JOSE_IMPLEMENTATION.md) for privacy controls, timeouts and how to enable real Claude extraction. To try draft review with explicitly synthetic local data instead:

```sh
docker compose exec backend python -m app.seed
```

Sign in, go to **Pending drafts**, and open submission `10000000-0000-4000-8000-000000000003` by ID. Verify or edit its highlighted unit price, then confirm; the sale then appears under **History**. Seeding is optional and never runs at startup.

## Results dashboard

The Results dashboard shows seven-logged-date averages, trends and sales-based planning guidance. See [the analysis and guidance report](docs/DIUSHEN_IMPLEMENTATION.md) for formulas, assumptions and contracts. To try it with synthetic history:

```sh
docker compose exec backend python -m app.seed_analysis
```

`AI_PROVIDER=mock` needs no paid key. Mock wording demonstrates the integration only, not real-provider quality.

## Isolated tests

This uses a separate Compose project, an unexposed PostgreSQL test service backed by tmpfs, and a unique schema per backend test. It does not use application database volumes.

```sh
docker compose -f compose.test.yml build
docker compose -f compose.test.yml run --rm backend-tests
docker compose -f compose.test.yml run --rm --no-deps frontend-checks
docker compose -f compose.test.yml --profile smoke up -d --build backend-smoke frontend-smoke
docker compose -f compose.test.yml --profile smoke run --rm --build http-smoke
docker compose -f compose.test.yml --profile smoke down
```

The test database is temporary. The normal application remains running. Use `docker compose stop` to stop the application while retaining its volumes.
