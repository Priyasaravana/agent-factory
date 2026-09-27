# Skills

A skill is a folder with a `SKILL.md` (frontmatter `name` + `description`, then
instructions) and optional supporting files and scripts. An agent loads a skill
when it's relevant. You choose which skills each agent may use in the workflow
editor.

| Source | Where | Changes by |
|---|---|---|
| Built in | `plugin/skills/`: the factory's own (`factory-station-contract`, `fastapi-golden-path`, `helm-kind-deploy`) | a PR to this repo |
| Default imports | `default_skills` in `.agent-factory/config.yaml`: BuilderIO/skills at a pinned commit, installed on first start | the pin in config (new installs), then the Skills page |
| Imported | a folder in any GitHub repo, stored in the factory DB and pinned to a commit | **Skills** page in the UI (or the API) |

## Default skills (BuilderIO)

The factory uses BuilderIO skills (plow-ahead, agent-watchdog,
efficient-frontier, read-the-damn-docs, quick-recap, stay-within-limits,
factory-recover), but none of them are copied into this repo. The config lists
them with a pinned commit:

```yaml
default_skills:
  - repo: BuilderIO/skills
    ref: main                      # followed by "check for update"
    sha: a74a3a0b8f400d2820351bcd695a7217dc88bec7
    license: MIT
    paths: [skills/plow-ahead, skills/agent-watchdog, ...]
```

- **At image build**, `agent-factory skills cache` fetches exactly those
  folders at that commit into `/opt/factory/skill-seeds`, along with a license
  NOTICE. First start therefore works without GitHub.
- **On first start**, each one is installed like any import. It shows as
  `github @a74a3a0` and is pinned by the workflow versions that use it. Outside
  Docker (no seed folder) it is fetched from GitHub at the pinned commit.
- **After that it's yours:** check for update, update or remove it on the
  Skills page. A skill that was ever installed is never re-installed or
  overwritten on restart. Changing the pin in the config only affects fresh
  installs.
- **Upgrading from a build that shipped them in the image:** they are installed
  on the next start. Older workflow versions, which have no pins, use the
  installed commit.

## Importing (UI: Skills)

1. **Review:** enter `owner/repo`, the folder that contains `SKILL.md`, and a
   branch, tag or commit. The factory fetches exactly that folder with git (a
   sparse, shallow checkout) and shows every file. Scripts (by extension or a
   `#!` line) are flagged.
2. **Install** saves the reviewed commit, not whatever the branch points to by
   then. If the skill has scripts, you must tick *I reviewed the scripts*.
3. The skill now appears in the agent editor's skill picker, as
   `github @<sha>`.

Private repos use `SKILLS_GITHUB_TOKEN` from `.env`: a read-only fine-grained
token with *Contents: read*. It is only put into the URL of a throwaway git
process, and removed right after the fetch. It is never stored or logged.

An import is **blocked** when:
- there is no `SKILL.md` or no description;
- the folder contains binary files;
- the name is invalid;
- the name clashes with a built-in skill;
- the same name is already installed from a different repo or folder (remove
  it first).

Limits: 200 files and 2 MB per folder.

## Pinning and updates

- Publishing a workflow version records the commit of every imported skill its
  agents use (`skill_pins`). A run materialises exactly those commits as a
  local plugin (`imported-skills`) next to the factory plugin, so a run is
  reproducible even after the skill is updated.
- **Check for update** fetches the skill's ref again and shows a diff against
  the installed commit. **Update** installs the new commit. Workflows keep the
  old commit until they are published again; the editor lists the pending
  skill updates and allows publishing just for them.
- **Remove** is blocked while a workflow's active version or draft uses the
  skill. Removed skills vanish from the picker, but every installed commit stays
  stored, so older workflow versions still run and can be audited.

## Safety

Skills are instructions; they grant no permissions. An agent can run a skill's
script only if its tool preset allows the shell, and every command still passes
the guardrail hooks (no pushes, no credentials, no writes outside the worktree,
observe-only agents stay observe-only). Review scripts as you would any code
dependency.

## API

| | |
|---|---|
| `GET /api/skills` | built-in + imported |
| `POST /api/skills/preview` `{repo, path, ref}` | files, scripts, problems, diff to installed |
| `POST /api/skills/install` `{repo, path, ref, sha, accept_scripts}` | install the reviewed commit |
| `GET /api/skills/{name}` | files, installed commits, workflows using it |
| `POST /api/skills/{name}/check-update` | preview of the ref's latest commit |
| `DELETE /api/skills/{name}` | remove from the picker |
