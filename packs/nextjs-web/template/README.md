# nextjs-web (stack pack)

Generated and maintained by the agent factory. See `docs/spec.md` for what this app does and
`AGENTS.md` for the conventions it follows.

    make verify                              # lint, types, tests (>=80% coverage), audit, build
    make run                                 # local dev server on :3000
    make preview                             # the production server on :3000, in-memory database
    make e2e BASE_URL=http://localhost:3000  # end-to-end + accessibility tests (Playwright)
