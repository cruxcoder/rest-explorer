# AI Workflow Skills

Prompt templates used during RestX development to enforce a structured AI-assisted workflow.

## What Are These?

"Skills" are reusable prompt templates that constrain an AI coding agent to follow a specific process. Rather than jumping straight to code, these skills force alignment and planning first - preventing the hallucination and architectural drift that comes from long, unstructured AI sessions.

| Skill | Purpose |
|-------|---------|
| `grill-me.md` | Relentless Q&A to reach shared understanding before coding begins |
| `prd-to-issues.md` | Convert a PRD into independently grabbable, vertically-sliced implementation issues |

## Origin

These templates are adopted from [Matt Pocock's AI Engineer Workshop](https://github.com/mattpocock/ai-engineer-workshop-2026-project) 

## How They Were Used

1. **Grill Me** - Before any feature work, the planning agent was instructed to interrogate the project requirements until a shared design concept was reached. This produced the alignment needed to write a coherent PRD.

2. **PRD to Issues** - After the PRD was written, this skill broke it down into vertical-slice issues (crossing schema -> API -> UI -> test layers) that could be handed off to the implementation agent (Cursor) one at a time, each in a fresh context window.

This two-step process kept every AI session inside the "Smart Zone" (~100k tokens) and ensured the human remained the architect while the AI remained the implementer.
