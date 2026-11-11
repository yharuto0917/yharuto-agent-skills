# Prompt Templates

Ready-to-use templates for composing questions to the adviser model.
Copy the relevant template and fill in the bracketed placeholders.

---

## Table of Contents

1. [Architecture Decision](#architecture-decision)
2. [Bug Investigation](#bug-investigation)
3. [Code Review](#code-review)
4. [Refactoring Strategy](#refactoring-strategy)
5. [Security Analysis](#security-analysis)
6. [Performance Optimization](#performance-optimization)
7. [Output Quality Review](#output-quality-review)
8. [Technology Selection](#technology-selection)
9. [General Advice](#general-advice)

---

## Architecture Decision

```
## Architecture Decision Request

**Project context**: [1-2 sentences about the project, its scale, and tech stack]

**Decision needed**: [The specific architectural choice to be made]

**Options I've identified**:
1. [Option A] — [brief pros/cons]
2. [Option B] — [brief pros/cons]
3. [Option C, if applicable] — [brief pros/cons]

**My initial lean**: [Which option you're leaning toward and why]

**Constraints**:
- [Team size / skill level]
- [Performance requirements]
- [Timeline / budget]
- [Existing infrastructure dependencies]

**Key files for context** (read these if needed):
- [path/to/relevant/file1]
- [path/to/relevant/file2]

**What I need**: A clear recommendation with reasoning, risk analysis,
and any implementation guidance.
```

---

## Bug Investigation

```
## Bug Investigation Request

**Symptom**: [What the user or system is experiencing]

**Reproduction**: [Steps or conditions that trigger the bug]
- Frequency: [always / intermittent / rare]
- Environment: [production / staging / local]

**What I've investigated so far**:
1. [Investigation step 1 — what you found]
2. [Investigation step 2 — what you found]
3. [Investigation step 3 — what you found]

**Hypotheses**:
- [Hypothesis A]: [evidence for/against]
- [Hypothesis B]: [evidence for/against]

**Relevant code paths**:
- [path/to/file1] — [what it does in this context]
- [path/to/file2] — [what it does in this context]

**What I need**: Help narrowing down the root cause, or validation of
my top hypothesis, plus a recommended fix approach.
```

---

## Code Review

```
## Code Review Request

**PR / Change summary**: [What this change does, in 2-3 sentences]

**Files changed**: [List the key files, or point to a diff]

**Areas of concern**:
1. [Specific concern — e.g., "Is the error handling comprehensive?"]
2. [Specific concern — e.g., "Does this handle concurrent access safely?"]
3. [Specific concern — e.g., "Is this the right abstraction level?"]

**Context the reviewer should know**:
- [Existing patterns in the codebase]
- [Performance requirements]
- [Known limitations or tech debt]

**Key files to read**:
- [path/to/changed/file1]
- [path/to/changed/file2]
- [path/to/related/existing/file] — [for understanding existing patterns]

**What I need**: Assessment of correctness, identification of edge cases
I might have missed, and suggestions for improvement.
```

---

## Refactoring Strategy

```
## Refactoring Strategy Request

**Current state**: [Describe the code/architecture as it exists now]

**Target state**: [What you want it to look like after refactoring]

**Motivation**: [Why this refactoring is needed — tech debt, performance,
maintainability, new requirements]

**Scope**: [How many files/modules/components are affected]

**Risk factors**:
- [Test coverage level]
- [Dependencies on the code being refactored]
- [Deployment constraints]

**Key files to examine**:
- [path/to/file1] — [role in the refactoring]
- [path/to/file2] — [role in the refactoring]

**What I need**: A phased migration plan with recommended ordering,
risk mitigation strategies, and any intermediate abstractions that
would make the transition safer.
```

---

## Security Analysis

```
## Security Analysis Request

**Feature / System**: [What you're securing]

**Threat model**: [What attacks or risks you're concerned about]
1. [Threat 1]
2. [Threat 2]
3. [Threat 3]

**Current defenses**: [What security measures are already in place]

**Proposed implementation**: [What you plan to do]

**Key files**:
- [path/to/auth/code]
- [path/to/validation/code]
- [path/to/config/file]

**What I need**: Identification of vulnerabilities I may have missed,
recommended defense-in-depth strategy, and specific implementation guidance.
```

---

## Performance Optimization

```
## Performance Optimization Request

**Performance issue**: [What's slow — specific endpoint, query, rendering]

**Current metrics**: [Response time, throughput, resource usage]

**Target metrics**: [What performance level is needed]

**What I've profiled / measured**:
- [Finding 1]
- [Finding 2]

**Optimization ideas I've considered**:
1. [Idea A] — [expected impact, implementation cost]
2. [Idea B] — [expected impact, implementation cost]

**Constraints**: [Can't change X, must maintain Y compatibility]

**Key files**:
- [path/to/bottleneck/code]
- [path/to/relevant/config]

**What I need**: Prioritized optimization recommendations with
expected impact and implementation guidance.
```

---

## Output Quality Review

```
## Output Quality Review Request

**Document type**: [RFC / ADR / API docs / user guide / etc.]

**Audience**: [Who will read this — engineers, PMs, customers, etc.]

**Document content**: [Paste the document or point to the file path]

**Review criteria**:
1. [Completeness — are all necessary sections covered?]
2. [Accuracy — are technical details correct?]
3. [Clarity — is it understandable by the target audience?]
4. [Actionability — can readers act on this information?]

**Specific concerns**: [Anything you're particularly unsure about]

**What I need**: Specific, actionable feedback on how to improve the document,
organized by priority (critical → nice-to-have).
```

---

## Technology Selection

```
## Technology Selection Request

**Need**: [What capability you're adding — e.g., "message queue", "ORM",
"frontend state management"]

**Candidates**:
1. [Option A] — [why it's a candidate]
2. [Option B] — [why it's a candidate]
3. [Option C] — [why it's a candidate]

**Evaluation criteria** (in priority order):
1. [Most important criterion — e.g., "performance at scale"]
2. [Second criterion — e.g., "team familiarity"]
3. [Third criterion — e.g., "maintenance burden"]

**Project context**:
- Tech stack: [languages, frameworks, infrastructure]
- Team: [size, experience level]
- Scale: [users, data volume, growth expectations]
- Timeline: [how soon this needs to be production-ready]

**What I need**: A clear recommendation with trade-off analysis,
migration/adoption considerations, and any gotchas from experience.
```

---

## General Advice

For situations that don't fit the templates above:

```
## Advice Request

**Context**: [What you're working on — 2-3 sentences]

**Situation**: [The specific challenge or decision you face]

**What I've considered**: [Your thinking so far]

**What I need**: [Specific type of advice — recommendation, review,
analysis, validation, brainstorming]
```

---

## Tips for Effective Questions

1. **Lead with the decision, not the backstory.** The adviser can read files
   if it needs more context. Start with what you need decided.

2. **Show your initial lean.** This gives the adviser something to react to.
   Even if your lean is wrong, it shows your reasoning and helps the adviser
   give targeted feedback.

3. **Quantify when possible.** "We have 50K daily active users" is better than
   "we have a lot of users." Numbers help the adviser calibrate their advice.

4. **List constraints explicitly.** The adviser can't guess that you have
   a 2-week deadline or that the team doesn't know Rust.

5. **Point to files, don't paste contents.** The adviser has read access to
   the workspace. Say "see `src/auth/middleware.ts`" instead of pasting 200
   lines of code into your question.
