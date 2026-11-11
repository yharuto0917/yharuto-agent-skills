# Escalation Guide

Detailed decision framework for each supported use case. Read this when you need
help deciding whether a specific situation warrants escalation.

---

## Table of Contents

1. [Architecture & Design Decisions](#1-architecture--design-decisions)
2. [Complex Bug Investigation](#2-complex-bug-investigation)
3. [Code Review & Quality Assessment](#3-code-review--quality-assessment)
4. [Refactoring Strategy](#4-refactoring-strategy)
5. [Security & Performance](#5-security--performance)
6. [Output Quality Check](#6-output-quality-check)
7. [Technology Selection](#7-technology-selection)
8. [Self-Assessment Checklist](#self-assessment-checklist)

---

## 1. Architecture & Design Decisions

**Escalate when:**
- Choosing between fundamentally different architectural patterns
  (monolith vs microservices, event-driven vs request-response)
- Designing data models that will be hard to change later
  (database schema, API contracts, message formats)
- Deciding on state management strategy for complex applications
- Making trade-offs between consistency, availability, and partition tolerance

**Don't escalate when:**
- Adding a new endpoint to an existing, well-established API pattern
- Creating a new component that follows the project's existing architecture
- Simple data model additions that mirror existing patterns

**Example escalation:**
> "I'm designing a notification system for a SaaS app. The options are:
> (A) polling-based with REST, (B) WebSocket push, (C) SSE.
> The app has ~10K concurrent users and needs real-time delivery within 5s.
> Current stack is Next.js + PostgreSQL. Which approach should I take?"

**Example non-escalation:**
> "I need to add a `/users/:id/preferences` endpoint. The existing API uses
> RESTful conventions with JSON responses." — This follows established patterns.

---

## 2. Complex Bug Investigation

**Escalate when:**
- The bug involves concurrency, race conditions, or deadlocks
- You've identified multiple possible root causes and can't narrow it down
- The bug spans multiple systems or services
- Reproduction requires understanding subtle timing or state dependencies
- The fix might have cascading side effects you can't fully predict

**Don't escalate when:**
- The stack trace clearly points to the issue
- It's a typo, off-by-one error, or missing null check
- The bug is in a well-understood code path with good test coverage

**Example escalation:**
> "Users intermittently get stale data after updating their profile. The update
> goes through an API → queue → worker pipeline. I see the DB is updated
> correctly, but the cache sometimes serves old data. The cache has a 60s TTL
> and I see cache invalidation events in the logs. Could this be a race between
> the invalidation and a concurrent read that repopulates the cache?"

---

## 3. Code Review & Quality Assessment

**Escalate when:**
- Reviewing a PR that touches core business logic or security-critical code
- The PR introduces a new pattern that the rest of the codebase will follow
- You're unsure whether the code handles edge cases correctly
- The PR involves complex algorithms or data structures

**Don't escalate when:**
- The PR is a straightforward feature addition following existing patterns
- It's a documentation-only change
- Simple dependency updates with no API changes

**Example escalation:**
> "PR #42 introduces a custom rate limiter using a sliding window algorithm.
> The implementation uses Redis sorted sets. I want to verify:
> (1) the algorithm is correct under concurrent access,
> (2) the Redis operations are atomic, and
> (3) the memory usage scales reasonably."

---

## 4. Refactoring Strategy

**Escalate when:**
- Planning a large-scale refactor that touches >10 files
- Migrating between frameworks or major versions
- Deciding the order and granularity of refactoring steps
- The refactor involves changing core abstractions

**Don't escalate when:**
- Renaming variables for clarity
- Extracting a single function or component
- Removing dead code

**Example escalation:**
> "The codebase has 15 React class components that need to be converted to
> functional components with hooks. Some share state via a custom context
> provider that uses lifecycle methods. What's the safest migration order,
> and should I refactor the context provider first or last?"

---

## 5. Security & Performance

**Escalate when:**
- Implementing authentication, authorization, or encryption
- Handling sensitive data (PII, payment info, credentials)
- Optimizing a query or algorithm with unclear performance characteristics
- Choosing between security approaches (CORS policies, CSP headers, input sanitization)

**Don't escalate when:**
- Adding standard input validation using well-known libraries
- Applying a security patch with clear instructions
- Simple query optimization with obvious indexing solutions

**Example escalation:**
> "I'm implementing file upload validation. The app accepts PDFs up to 50MB.
> I need to prevent: (1) path traversal, (2) malicious file content,
> (3) ZIP bombs disguised as PDFs, (4) SSRF via PDF rendering.
> What's the defense-in-depth strategy here?"

---

## 6. Output Quality Check

**Escalate when:**
- You've written a technical document (RFC, ADR, API docs) and want a quality check
- The output will be read by external stakeholders or customers
- You're unsure if your explanation is clear and complete
- The document involves nuanced technical trade-offs

**Don't escalate when:**
- Writing inline code comments
- Generating standard boilerplate documentation
- Simple README updates

**Example escalation:**
> "I've drafted an Architecture Decision Record for switching from REST to gRPC
> for internal service communication. Can you review it for:
> (1) completeness of alternatives considered,
> (2) accuracy of trade-off analysis,
> (3) clarity for the intended audience (senior engineers)?"

---

## 7. Technology Selection

**Escalate when:**
- Choosing a framework, library, or tool that will be a long-term dependency
- Comparing options with similar capabilities but different trade-offs
- The selection involves licensing, support, or ecosystem considerations
- Migration cost from the chosen option would be high if it's wrong

**Don't escalate when:**
- Adding a small utility library with no lock-in
- Using a tool you've already evaluated for this project
- The project already has a standard for this type of dependency

**Example escalation:**
> "For the new data pipeline, I'm choosing between:
> (A) Apache Kafka — mature, complex, high throughput
> (B) NATS JetStream — simpler, growing ecosystem, good enough throughput
> (C) Redis Streams — we already have Redis, simpler ops
> Volume is ~50K events/sec, need at-least-once delivery, team of 3 devs."

---

## Self-Assessment Checklist

Run through this quick checklist when you're unsure about escalating:

| Question | If YES → |
|---|---|
| Could a wrong decision here require >2 hours of rework? | Escalate |
| Am I choosing between 3+ viable approaches with no clear winner? | Escalate |
| Does this involve security, data integrity, or user privacy? | Escalate |
| Would I want a code review from a senior engineer on this? | Escalate |
| Is this the first time this pattern is used in the project? | Consider escalating |
| Am I following an established pattern in the codebase? | Don't escalate |
| Is the task straightforward with a single obvious approach? | Don't escalate |
| Is speed more important than depth for this task? | Don't escalate |

When multiple "escalate" signals fire, definitely escalate. When signals are mixed,
lean toward escalating — the cost of a quick consultation is much lower than the
cost of a bad decision.
