# Agents

Subagent definitions, one `<name>.md` per agent, with frontmatter:

```markdown
---
name: <kebab-case-name>
description: <when Claude should delegate to this agent>
tools: Read, Grep, Glob   # optional; omit to inherit all
model: sonnet            # optional
---

<system prompt>
```

None yet.
