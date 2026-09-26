---
id: secure-coding
title: Secure coding standard
---
- Validate every input at the API boundary with pydantic models; reject unknown fields where practical.
- Use the ORM or bound parameters for all queries; never format SQL strings.
- Never log or return secrets, tokens or full request bodies containing credentials.
- Return generic error messages to clients; log details server-side.
- Containers run as non-root with a read-only root filesystem.
- Dependencies are pinned in `uv.lock`; do not add packages without a clear need.
