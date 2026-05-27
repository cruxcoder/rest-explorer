Break a PRD into independently-workable issues and output each as a markdown document for the user to save. Use when the user wants to turn a PRD into a list of concrete tasks.

# PRD to Issues

Break a PRD into independently-grabbable issues using vertical slices (tracer bullets), output as markdown documents.

## Process

### 1. Locate the PRD
The user will provide the PRD content below. Do not ask for it again.

### 2. Draft vertical slices
Break the PRD into **tracer bullet** issues. Each issue is a thin vertical slice that cuts through ALL integration layers end-to-end, NOT a horizontal slice of one layer.

Slices may be 'HITL' or 'AFK'. HITL slices require human interaction, such as an architectural decision or a design review. AFK slices can be implemented and merged without human interaction. Prefer AFK over HITL where possible.

<vertical-slice-rules>
- Each slice delivers a narrow but COMPLETE path through every layer (schema, API, UI, tests)
- A completed slice is demoable or verifiable on its own
- Prefer many thin slices over few thick ones
</vertical-slice-rules>

### 3. Quiz the user
Present the proposed breakdown as a numbered list. For each slice, show:

- **Title**: short descriptive name
- **Type**: HITL / AFK
- **Blocked by**: which other slices (if any) must complete first
- **User stories covered**: which user stories from the PRD this addresses

Ask the user:

- Does the granularity feel right? (too coarse / too fine)
- Are the dependency relationships correct?
- Should any slices be merged or split further?
- Are the correct slices marked as HITL and AFK?

Iterate until the user approves the breakdown.

### 4. Output the issue content
For each approved slice, output the markdown content using the naming pattern `issues/NNN-short-title.md` (e.g. `issues/001-add-user-auth.md`).

Number issues starting from 001.

Output issues in dependency order (blockers first) so you can reference real filenames in the "Blocked by" field.

Do NOT use `gh issue create` or any GitHub CLI commands. Do NOT reference GitHub issue numbers. Use local filenames for all cross-references.

IMPORTANT: You cannot write files directly. Output the FULL content of EACH issue in your response, clearly labeled with its filename. Tell the user to copy each one and save it as the corresponding file. Do NOT say you have saved them. Just output the text.

<issue-template>
## Parent PRD

`issues/prd.md`

## What to build

A concise description of this vertical slice. Describe the end-to-end behavior, not layer-by-layer implementation. Reference specific sections of the parent PRD rather than duplicating content.

## Acceptance criteria

- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Criterion 3

## Blocked by

- Blocked by `issues/NNN-title.md` (if any)

Or "None - can start immediately" if no blockers.

## User stories addressed

Reference by number from the parent PRD:

- User story 3
- User story 7
</issue-template>

Do NOT close or modify the parent PRD file.

---

**HERE IS THE PRD CONTENT:**

[PASTE YOUR FULL PRD TEXT HERE]
