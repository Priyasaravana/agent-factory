# ADR-0005: Model access is switchable by configuration

**Status:** accepted · 2026-09-25

## Decision
The factory authenticates with whichever credential is present in `.env`:

| Option | Variable | Good for |
|---|---|---|
| Claude subscription | `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) | personal local use (the current choice) |
| Anthropic API | `ANTHROPIC_API_KEY` | team use, CI, predictable automation |
| Amazon Bedrock | `CLAUDE_CODE_USE_BEDROCK=1` + AWS creds | cost and governance on AWS |

## Caveats
- A subscription is one person's usage window. A factory makes many calls
  back to back. The engine pauses runs when the SDK reports a rate-limit
  rejection and resumes when the window resets, but throughput is capped.
- Check Anthropic's current terms for using subscription auth with the Agent
  SDK. For anything shared with others, use an API key or Bedrock.
- Model tiers are aliases (`opus`, `sonnet`, `haiku`) configured in
  `config.yaml → models`.
