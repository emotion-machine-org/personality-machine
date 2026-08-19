# Web Dashboard

Next.js 15 app providing the companion builder UI and debugging dashboard: create and configure companions, edit prompts, inspect conversations/memory, and test voice sessions.

## Setup

```bash
npm install
cp .env.example .env.local   # fill in Clerk keys and API URL
npm run dev                  # http://localhost:3000
```

Authentication uses [Clerk](https://clerk.com). You need a Clerk application and the following env vars (see `.env.example`):

- `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` / `CLERK_SECRET_KEY`
- `NEXT_PUBLIC_CLERK_JWT_TEMPLATE` — the JWT template name used to authenticate against the backend API
- `NEXT_PUBLIC_API_URL` — the FastAPI server (default `http://localhost:8100`)

## Commands

```bash
npm run dev      # Development server
npm run build    # Production build
npm run lint     # ESLint (pre-commit enforces max-warnings=0)
npm run test     # Vitest
```

## Structure

- `src/app/(authenticated)/` — protected routes (dashboard root, API keys, relationships, share)
- `src/app/companion/` — public companion interface
- `src/components/` — UI components (auth, dashboard, memory-explorer, voice, ...)
- `src/hooks/` — React Query-based data hooks

The API reference served at `/API_V2_REFERENCE.md` is a copy of `server/API_V2_REFERENCE.md` (the canonical source) — keep them in sync.
