# Bodega

Gabriel Marte's data/state, draft-review, and confirmed-history implementation.
See [the implementation report](docs/GABRIEL_IMPLEMENTATION.md) for the API contract, design decisions, file inventory, validation results, and team integration boundaries.

## Start locally

Requires Docker Desktop with Linux containers and Docker Compose.

```sh
docker compose up --build -d --wait
```

Open http://localhost:5173. The development owner access code is `local-development-code-123`, matching the existing Compose configuration. API documentation is at http://localhost:8000/docs. Schema migrations run automatically during backend startup.

The ingestion UI is pending team integration. To try draft review with explicitly synthetic local data:

```sh
docker compose exec backend python -m app.seed
```

Connect in the frontend and open submission `10000000-0000-4000-8000-000000000003`. Verify or edit its highlighted unit price, then confirm. The confirmed ledger refreshes automatically. Seeding is optional and never runs at startup.

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
