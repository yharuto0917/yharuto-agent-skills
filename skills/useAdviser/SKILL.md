---
name: useAdviser
description: >
  Use before committing to a high-stakes technical decision or delivering a careful/thorough review, even if no model is named explicitly. Covers: architecture or system design choices (frameworks, service boundaries, layered vs clean architecture), database/schema design where correctness or money is critical, technology/library selection (e.g. picking between frameworks, databases, state-management libraries), security or vulnerability audits, complex/hard-to-diagnose bug investigations, and review of risky code changes (concurrency, race conditions, infra/IaC templates, auth). Also trigger on requests for a second opinion, deeper scrutiny, multiple perspectives, or an explicit ask to consult a senior/higher-tier/more capable model — e.g. ask the adviser, consult Opus, 上位モデルに聞いて, アドバイザーに相談, Opusに監査させて, 念入りに確認して, 丁寧にレビューして, 複数の視点から調査して. Escalate to a stronger read-only model as a subagent for expert input before finalizing.
---

# useAdviser

You are a capable model, but some decisions benefit from a second opinion by a more
powerful model. This skill teaches you **when** and **how** to escalate to a senior
model, and **how to integrate** its advice into your work.

The core idea is simple: rather than guessing on high-stakes decisions, you spin up
a read-only Subagent running a stronger model, ask it a focused question, and apply
its answer. Think of it like a junior developer pinging a staff engineer on Slack —
you do the work, but you get expert input on the tricky parts.

---

## When to Escalate

There are four triggers. Any one of them is sufficient.

### Trigger 1: Explicit Skill Activation / Call

The user has explicitly activated or called this skill (e.g., using a slash command like `/useAdviser`, selecting the skill via the IDE UI, or mentioning the skill name directly). When you are invoked *with* this skill explicitly chosen, you must proceed to define the adviser and consult it immediately.

### Trigger 2: User Explicit Request

The user directly asks you to consult a higher model. Look for phrases like:
- "Ask the adviser / advisor"
- "Consult Opus / Pro / the senior model"
- "Get a second opinion"
- "上位モデルに聞いて" / "アドバイザーに相談して"
- "Have someone more senior look at this"

When this trigger fires, **always** escalate — no judgment call needed.

### Trigger 3: Task Category Match

The user (or the project's configuration) has defined rules mapping task categories
to escalation. Check for these rules in:
- User rules / system prompt (look for "adviser" or "useAdviser" configuration)
- Project-level `.gemini/settings.json` or equivalent

Typical rule format:
```
adviser:
  default_model: "claude-opus"
  rules:
    - task: "architecture"
      model: "claude-opus"
    - task: "code-review"
      model: "gemini-pro"
```

If the current task matches a defined category, escalate to the specified model.

### Trigger 4: Self-Assessment

This is the most nuanced trigger. Escalate when you recognize:

- **High uncertainty**: You can see multiple valid approaches but can't confidently
  rank them. Architecture decisions, DB schema design, API surface design.
- **High risk**: A wrong call could cause security vulnerabilities, data loss,
  or significant rework. Security audits, migration strategies.
- **Domain depth**: The problem requires deep expertise in a specialized area
  (cryptography, distributed systems consensus, compiler optimization).
- **Quality gate**: You've produced output (docs, code review, design) and want
  a quality check before presenting it to the user.

**Do NOT escalate for:**
- Routine coding tasks you're confident about
- Simple bug fixes with clear root causes
- Straightforward file operations or formatting
- Tasks where speed matters more than depth

When in doubt, a quick mental test: "If I got this wrong, how bad would it be?"
If the answer is "the user would need to redo significant work" — escalate.

> For a detailed decision framework with examples for each of the 7 supported
> use cases, read `references/escalation-guide.md`.

---

## How to Escalate

### Step 1: Define the Adviser Subagent

Define a subagent with **read-only** capabilities. The adviser should be able to
examine the codebase but should not modify it — you retain control of all writes.

```
define_subagent:
  name: "adviser"
  description: "Senior model adviser for high-stakes technical decisions"
  system_prompt: |
    You are a senior technical adviser. Your role is to provide expert guidance
    on complex technical decisions. You have read-only access to the codebase.

    Your responses should be:
    1. Direct and actionable — give a clear recommendation, not just options
    2. Well-reasoned — explain WHY your recommendation is best
    3. Risk-aware — call out potential pitfalls and edge cases
    4. Concrete — include specific implementation guidance when relevant

    Format your response as:
    ## Recommendation
    [Your clear recommendation]

    ## Reasoning
    [Why this is the best approach]

    ## Risks & Mitigations
    [What could go wrong and how to handle it]

    ## Implementation Notes
    [Specific guidance for the implementing model]
  enable_write_tools: false
  enable_mcp_tools: false
```

### Step 2: Compose the Question

A well-structured question gets a much better answer. Follow this pattern:

```
**Context**: [What project/feature you're working on — 2-3 sentences max]
**Current Task**: [What you're trying to accomplish right now]
**Decision Point**: [The specific question you need answered]
**Options Considered**: [What you've thought of so far, with your initial lean]
**Constraints**: [Technical, business, or time constraints that matter]
**What I Need**: [The specific output format — recommendation, review, analysis]
```

Key principles:
- **Compress context aggressively.** The adviser doesn't need your full history.
  Summarize the relevant code structure, don't dump entire files.
- **Be specific.** "Should I use JWT or sessions?" is better than "How should I
  do auth?"
- **Show your work.** Share what you've already considered — the adviser can
  build on it rather than starting from scratch.

> For ready-to-use templates for each use case (architecture, code review, etc.),
> read `references/prompt-templates.md`.

### Step 3: Invoke and Wait

```
invoke_subagent:
  TypeName: "adviser"
  Role: "Senior Technical Adviser"
  Prompt: [your composed question]
  Workspace: "inherit"
```

The adviser runs in the same workspace so it can read your code. It returns its
advice as a message.

### Step 4: Integrate the Advice

Once you receive the advice:

1. **Adopt by default.** The user has indicated that adviser recommendations should
   be followed. Apply the advice to your work.

2. **Override only with cause.** If the advice contradicts known context that the
   adviser couldn't see (e.g., a user constraint not in the codebase), you may
   deviate — but you must explain why to the user.

3. **Synthesize, don't just copy.** The adviser gives high-level guidance; you
   translate it into concrete code changes, specific refactors, or detailed
   implementation steps appropriate to the current context.

> For detailed patterns on how to integrate different types of advice
> (design decisions, code review feedback, refactoring plans, etc.),
> read `references/integration-patterns.md`.

---

## Reporting to the User

Transparency is essential. After every escalation, report to the user:

### Minimal Report (inline)

For quick consultations:
```
> 💡 **Adviser consulted** on [topic]. Recommendation: [1-sentence summary].
> Applying this to the implementation.
```

### Detailed Report (for significant decisions)

For architecture or design decisions:
```
## Adviser Consultation: [Topic]

**Question asked**: [Brief summary of what you asked]
**Recommendation**: [What the adviser suggested]
**Reasoning**: [Key points from the adviser's reasoning]
**Action taken**: [How you're applying this to the current work]
```

If you chose NOT to follow the advice (which should be rare), add:
```
**Deviation**: [What you did differently and why]
```

---

## Configuration

Users can customize escalation behavior through their rules or settings:

### Model Mapping

Specify which upper model to use for which task type:
```yaml
adviser:
  default_model: "claude-opus"
  rules:
    - task: "architecture"
      model: "claude-opus"
    - task: "code-review"
      model: "gemini-pro"
    - task: "security"
      model: "claude-opus"
    - task: "performance"
      model: "gemini-pro"
    - task: "quality-check"
      model: "claude-opus"
    - task: "tech-selection"
      model: "gemini-pro"
```

### Escalation Sensitivity

Users can adjust how aggressively you escalate:
```yaml
adviser:
  sensitivity: "high"    # escalate proactively on any uncertainty
  # sensitivity: "medium" # escalate on significant decisions (default)
  # sensitivity: "low"    # escalate only on explicit request or critical risk
```

### Fallback Behavior

When no model mapping is configured:
- If the user has previously mentioned a preferred model, use that
- Otherwise, define the adviser subagent without specifying a model and let
  the platform choose its default (which is typically the most capable available)
- Always inform the user which model was consulted in your report

---

## Anti-Patterns

Avoid these common mistakes:

1. **Over-escalation.** Don't consult the adviser for every minor decision.
   This wastes tokens and slows you down. Reserve it for genuinely complex
   or high-risk situations.

2. **Under-specification.** Sending vague questions like "what do you think?"
   produces vague answers. Always structure your question with context,
   decision point, and constraints.

3. **Context dumping.** Don't paste entire files into the question. Summarize
   the relevant parts. The adviser can read files itself if needed.

4. **Ignoring the advice.** If you asked for advice, take it seriously.
   Consistently ignoring adviser output defeats the purpose. If you find
   yourself wanting to ignore it, reconsider whether you asked the right
   question.

5. **Not reporting.** Always tell the user when you've consulted the adviser.
   Silent escalation erodes trust.
