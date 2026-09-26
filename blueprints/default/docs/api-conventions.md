---
id: api-conventions
title: REST API conventions
---
- Resources are plural nouns: `/bookmarks`, `/bookmarks/{id}`.
- Create returns **201** with the created resource; missing ids return **404**
  with `{"detail": "..."}`; validation errors are FastAPI's default **422**.
- List endpoints return newest first and accept `limit` (default 50, max 200)
  and `offset`.
- Filters are query parameters named after the field (`?tag=python`).
- Timestamps are ISO 8601 UTC (`created_at`, `updated_at`), set by the server.
- Never return internal errors or stack traces to clients.
