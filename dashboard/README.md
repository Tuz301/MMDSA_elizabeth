# MMDSA supervisor dashboard

React + TypeScript + Vite. The supervisor's daily view of the system: the
unacknowledged-positives number, the alert worklist, the EID cascade, the
home-visit review queue, and enrolment.

## Running it

```bash
npm install
cp .env.example .env.local   # token mode by default, for development
npm run dev                  # proxies /api to http://localhost:8000
npm run build                # type-check + production build
npm test
```

Develop against a local backend:

```bash
cd ../backend
USE_SQLITE=1 ./.venv/bin/python manage.py migrate
USE_SQLITE=1 ./.venv/bin/python manage.py seed_geography
USE_SQLITE=1 ./.venv/bin/python manage.py seed_programme
USE_SQLITE=1 ./.venv/bin/python manage.py runserver
```

## Three decisions that shape the code

**Identifier fields are optional in the types.** The server removes names,
telephone numbers, addresses and coordinates from responses for roles outside
the identifier-read set. The hand-written types mark those fields optional,
so the compiler forces every screen to handle the code-only view — which is
the normal, designed state for an LGA coordinator or state manager, not an
error. `displayName()` in `src/lib/format.ts` is the single rendering path.

**Red means one thing.** The only red on any screen is an unacknowledged
positive result or a breached deadline. Everything else uses neutral tones,
so the colour keeps its meaning.

**Entry and acknowledgement are separate buttons.** The gap between them is
the relay delay — the pilot's primary indicator — and the interface must not
collapse what the measurement depends on. The result-entry dialog says so in
its own words.

## Authentication

`VITE_AUTH_MODE=cognito` (the pilot): username/password over SRP against the
Cognito pool, including the forced first-password change for new accounts.
`VITE_AUTH_MODE=token` (development): paste a bearer token at the sign-in
screen. The access token lives in sessionStorage — it survives a refresh,
dies with the tab, and never outlives the working session on a shared
facility computer.

In production the dashboard is served from the same origin as the API, so no
CORS configuration exists anywhere.
