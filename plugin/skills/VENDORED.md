# Vendored skills

These skills are copied **unmodified** from [BuilderIO/skills](https://github.com/BuilderIO/skills)
(MIT, see `LICENSE.builderio`), pinned at commit
`a74a3a0b8f400d2820351bcd695a7217dc88bec7`.

| Skill | Used by | Why |
|---|---|---|
| plow-ahead | intake, developer | Autonomy between the human gates: assumptions, not questions |
| agent-watchdog | verifier | Independent second investigation at Acceptance |
| efficient-frontier | developer | Expensive model for judgment, cheap sub-agents for mechanics |
| read-the-damn-docs | intake, architect, developer, devops | Check authoritative docs before guessing |
| quick-recap | (available) | Clear status at the end of work |
| stay-within-limits | (reference) | The engine enforces this natively from SDK rate-limit events |
| factory-recover | (reference) | The engine's restart rule: mark interrupted, resume only with authorization |

Project-specific guidance is layered on with `skill_prompts` in
`.agent-factory/config.yaml` — never by editing these files. To upgrade, re-copy
from a newer upstream commit and update the pin above.

Skills authored in this repo: `factory-station-contract`, `fastapi-golden-path`,
`helm-kind-deploy`.
