# my-public-claude-skills

A personal collection of [Claude Code](https://claude.com/claude-code) skills, agents, slash commands and
project instructions, published for reuse. Everything here is generic: project names, people and paths from the
projects these were developed on have been removed or replaced with `{{PLACEHOLDERS}}`.

## Contents

| Kind | Name | What it does |
|---|---|---|
| Skill | [`project-manager`](skills/project-manager/SKILL.md) | Plan and run a multi-agent software project on one repository: a git-backed task board, task cards with disjoint file ownership and explicit prerequisites, one card per agent context in a private git worktree, merged back through a board tool that enforces claims. Includes the board/start/finish/launch scripts (Python, standard library only) and the invoker prompt. |

## Repository layout

```
.
├── .claude-plugin/        plugin + marketplace manifests (install the whole repo as a Claude Code plugin)
├── skills/                one folder per skill: SKILL.md + optional reference/, templates/, scripts/
│   └── project-manager/
├── agents/                subagent definitions, one <name>.md each (frontmatter: name, description, tools, model)
├── commands/              slash commands, one <name>.md each
├── hooks/                 hook scripts plus a snippet of the settings.json that wires them
├── instructions/          reusable CLAUDE.md sections and conventions to paste into a project
└── docs/                  longer write-ups: design notes, measurements, how the pieces fit together
```

Conventions for new additions:

- **Self-contained.** A skill folder carries everything it needs; it never references a file outside itself.
- **No private data.** Owner names, project names, paths, emails, tokens and hostnames become `{{PLACEHOLDERS}}`
  or neutral wording ("the owner", "a real project"). Check with a grep before every commit.
- **One line in the table above** per skill, agent, command or hook.

## Install

**As a plugin** (skills, agents and commands together):

```
/plugin marketplace add pedro-lage/my-public-claude-skills
/plugin install my-public-claude-skills@my-public-claude-skills
```

**One skill by hand**: copy its folder into `~/.claude/skills/` (every project) or `<project>/.claude/skills/`
(one project).

```
git clone https://github.com/pedro-lage/my-public-claude-skills
cp -r my-public-claude-skills/skills/project-manager ~/.claude/skills/
```

## Using `project-manager`

In a repository without a `plan/` folder, ask Claude to "set up a task board" (bootstrap: it copies the templates
into `plan/` and fills the placeholders), then to "plan the work" (writes task cards and board rows). Each agent
context then runs "invoke" to claim and execute exactly one card. See [SKILL.md](skills/project-manager/SKILL.md)
and [reference/planning.md](skills/project-manager/reference/planning.md) for the full protocol.

## License

MIT, see [LICENSE](LICENSE).
