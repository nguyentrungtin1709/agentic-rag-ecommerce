---
name: develop-feature
description: Full development workflow from planning through deployment for AI Agent features
---

# Develop Feature Workflow

Follow this 5-phase workflow when developing new features or making significant changes.

## Phase 1: Planning & Design

1. **Define agent goals**: Clarify what the feature should accomplish, scope, and constraints
2. **Choose architecture pattern**:
   - **ReAct**: Single-agent tool-use loops
   - **Plan-and-Execute**: Tasks requiring upfront planning
   - **Multi-Agent**: Complex workflows with specialized agents
3. **Design tool integration**: Identify APIs needed, define typed schemas
4. **Define evaluation criteria**: Success metrics before coding
5. **Externalize prompts**: Draft prompts as separate files
6. **Analyze requirements**: Check for SOLID violations
7. **Create decision record**: Run `/create-decision-record`

## Phase 2: Core Development

1. **Start simple**: Begin with minimal working implementation
2. **Build modularly**: Develop each layer in isolation:
   - Prompt templates → LLM client → Tools → Orchestration → Memory
3. **Write tests**: TDD or alongside implementation
4. **Version prompts**: Treat prompt changes as code changes
5. **Commit frequently**: Use Conventional Commits
6. **Refactor immediately**: Address code smells right away

## Phase 3: Testing & Evaluation

1. **Unit tests**: Test individual components in isolation
2. **Integration tests**: Verify tool calls, error handling
3. **Agent evaluation**: Run end-to-end scenarios
   - LLM-as-judge for subjective quality
   - Deterministic checks for structured outputs
4. **Prompt regression**: Re-run evaluation after prompt changes
5. **Edge case testing**: Adversarial inputs, API failures
6. **Cost profiling**: Measure token usage and latency

## Phase 4: Debugging & Observability

1. **Enable tracing**: Instrument with session → trace → span
2. **Inspect tool calls**: Verify correct tools and arguments
3. **Identify bottlenecks**: Find latency hotspots
4. **Log structured data**: Model, tokens, latency, version
5. **Iterate on prompts**: Use evidence to refine

## Phase 5: Deployment & CI

1. **Self-review**: Run code review checklist
2. **Run tests**: Ensure all pass
3. **Update docs**: README, API docs if needed
4. **Create PR**: With clear description
5. **Monitor**: Track quality and cost metrics
