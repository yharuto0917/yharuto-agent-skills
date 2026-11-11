# Integration Patterns

How to integrate advice from the upper model back into your work.
The general principle is **adopt by default, deviate with cause**.

---

## Table of Contents

1. [General Integration Flow](#general-integration-flow)
2. [Architecture Decisions](#architecture-decisions)
3. [Bug Fix Guidance](#bug-fix-guidance)
4. [Code Review Feedback](#code-review-feedback)
5. [Refactoring Plans](#refactoring-plans)
6. [Security Recommendations](#security-recommendations)
7. [Quality Feedback](#quality-feedback)
8. [Technology Recommendations](#technology-recommendations)
9. [Handling Disagreements](#handling-disagreements)

---

## General Integration Flow

```
1. Receive advice from the adviser
2. Parse the recommendation, reasoning, and implementation notes
3. Assess compatibility with current context
   - Does it conflict with any known user constraints?
   - Does it require capabilities or dependencies not available?
4. If compatible → apply directly
5. If partially compatible → adapt the core recommendation to fit constraints
6. If incompatible → explain to user why you're deviating
7. Report what advice was received and how it was applied
```

### Key Mindset

The adviser has broader knowledge but less context about the specific
conversation history. You have deep context but (by definition) less
capability on the hard parts. The best outcomes come from combining
both: use the adviser's strategic insight with your tactical context.

---

## Architecture Decisions

**When the adviser recommends an approach:**

1. Adopt the recommended architecture
2. Create a brief design summary showing the decision and rationale
3. Implement the architecture, following any specific guidance
4. If the adviser mentioned risks, proactively add mitigations
   (error handling, fallback logic, monitoring hooks)

**What to do with multiple recommendations:**

Sometimes the adviser says "either A or B works, but A is slightly better
for your scale." In this case, go with A — the adviser's slight preference
is informed by deeper expertise.

**Partial adoption:**

If the adviser recommends a 3-phase migration but you're only implementing
phase 1 right now, that's fine — adopt phase 1 fully and document phases 2-3
as future work. Don't half-implement the full plan.

---

## Bug Fix Guidance

**When the adviser identifies the root cause:**

1. Verify the hypothesis by examining the specific code paths mentioned
2. Implement the fix as recommended
3. Add a test that covers the failure mode
4. Check for similar patterns elsewhere in the codebase

**When the adviser suggests investigation steps:**

1. Follow the suggested investigation order
2. Report findings back to the user (not to the adviser — don't re-escalate
   unless the investigation reveals something unexpected)
3. Once root cause is confirmed, implement the fix

**When the adviser is uncertain:**

Even an "I think it might be X, but check Y too" is valuable. Prioritize
investigation X, then Y. The adviser's uncertainty still narrows the search
space significantly.

---

## Code Review Feedback

**For specific code issues (bugs, edge cases):**

1. Fix each identified issue
2. For each fix, add a brief comment explaining why (if not obvious)
3. If the adviser identified a pattern of issues (e.g., "error handling is
   inconsistent throughout"), apply the fix systematically, not just to
   the specific lines mentioned

**For design suggestions (abstractions, naming, structure):**

1. Apply the suggestion if it's contained within the scope of the current change
2. If it would require significant changes outside the current scope,
   note it as a follow-up task rather than expanding scope
3. Apply naming suggestions directly — they're cheap and improve readability

**For "consider this alternative" suggestions:**

The adviser may say "this works, but have you considered using X instead?"
This is usually worth adopting if the alternative is cleaner or more
idiomatic, even if the current approach works. The adviser has seen more
codebases and recognizes better patterns.

---

## Refactoring Plans

**When the adviser provides a phased plan:**

1. Follow the phase order as recommended — it exists for a reason
   (usually dependency ordering or risk minimization)
2. Complete each phase fully before moving to the next
3. Run tests after each phase to catch regressions early
4. If a phase reveals unexpected complexity, note it but continue
   the plan rather than ad-hoc deviating

**When the adviser recommends intermediate abstractions:**

Sometimes the adviser says "create an adapter layer first, then migrate
consumers one by one." This intermediate step may feel like extra work,
but it's usually the safest path. Create the abstraction even if it
feels like overhead.

**Scope adjustments:**

If the adviser's plan is more ambitious than what the user asked for,
implement only what matches the user's scope. But mention the full plan
to the user — they may want to extend the scope.

---

## Security Recommendations

**Security advice should almost never be overridden.** The cost of a
security vulnerability far exceeds the cost of a slightly more complex
implementation.

1. Implement all recommended security measures
2. If the adviser recommends defense-in-depth (multiple layers),
   implement ALL layers, not just the "most important" one
3. Add security-related comments explaining WHY each measure exists
4. If the adviser mentions specific attack vectors, add tests that
   verify the defenses work

**The one exception:** If implementing a security measure requires a
dependency or capability that isn't available in the project, note this
to the user as a known limitation rather than silently skipping it.

---

## Quality Feedback

**When reviewing your own output (docs, designs, etc.):**

1. Apply all "critical" and "important" feedback directly
2. For "nice-to-have" suggestions, apply them if they don't significantly
   change the scope or direction
3. If the adviser says "this section is unclear," rewrite it from scratch
   rather than patching — unclear writing usually needs restructuring,
   not just word changes

**Iterating:**

You should NOT re-escalate for a quality check on the revised version
unless the user specifically asks. One round of quality feedback is
typically sufficient. Apply the feedback thoughtfully and trust that
the improvements address the issues.

---

## Technology Recommendations

1. Adopt the recommended technology
2. If the adviser mentions a specific version or configuration, use it
3. Set up the technology following the adviser's implementation guidance
4. If the adviser called out gotchas or pitfalls, add comments or config
   to avoid them proactively

**When the recommendation surprises you:**

If the adviser recommends something you didn't consider as a candidate,
that's usually the most valuable advice. The adviser has broader
knowledge of the ecosystem. Trust the recommendation and explain to
the user why this option emerged.

---

## Handling Disagreements

Situations where you might reasonably deviate from adviser's advice:

### 1. Context the adviser couldn't see

The adviser doesn't have your conversation history. If the user said
"don't use library X" and the adviser recommends library X, follow
the user's constraint. Report this:

> "The adviser recommended [X], but this conflicts with your earlier
> instruction to avoid [X]. I'm using [Y] instead, which aligns with
> the adviser's reasoning but respects your constraint."

### 2. Scope mismatch

The adviser might recommend a comprehensive solution when the user
wanted something quick. Scale down to match the user's intent, but
note the comprehensive option:

> "The adviser recommended a full [X] setup. For your current needs,
> I've implemented a simpler version that covers [the essentials].
> The full setup is documented as a future enhancement."

### 3. Practical impossibility

If the recommendation requires something not available (a specific
service, API, or tool not in the environment):

> "The adviser recommended [X], which requires [Y]. Since [Y] isn't
> available in this environment, I've adapted the approach to use [Z],
> which achieves the same goal."

### What NOT to do

- Don't silently ignore advice
- Don't override advice based on "I think I know better" — you
  escalated because you recognized the task needed a stronger model
- Don't partially apply advice (e.g., implementing 2 of 3 recommended
  security layers) without explaining why
