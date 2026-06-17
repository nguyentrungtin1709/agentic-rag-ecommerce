# Design Principles

## SOLID Principles (REQUIRED for all OOP code)

### S - Single Responsibility Principle (SRP)
- Each class/module should have **only one reason** to change.
- Separate layers: **UI/Presentation** <-> **Business Logic** <-> **Data Access**.
- Don't write API calls directly in event handlers. Extract into a separate `Service` class.

### O - Open/Closed Principle (OCP)
- Software entities should be **open for extension** but **closed for modification**.
- Use `interface` or `abstract class` to define common behaviors.
- Add new features by creating new implementations, not modifying existing code.

### L - Liskov Substitution Principle (LSP)
- Objects of a subclass must be able to **replace the parent class** without breaking the program.
- Ensure subclasses properly implement the contract of their interface/parent class.

### I - Interface Segregation Principle (ISP)
- **Do not force** clients to depend on interfaces they don't use.
- Split large interfaces into smaller, specialized interfaces.

### D - Dependency Inversion Principle (DIP)
- High-level modules should not depend on low-level modules. Both should depend on **abstractions**.
- Use **Dependency Injection (DI)** to inject dependencies instead of instantiating directly.

---

## AI Agent Development Strategy

### Modularity
- Design agent workflows as independent, composable components: `Input Parsing` -> `Prompt Construction` -> `LLM Call` -> `Tool Execution` -> `Response Synthesis`.
- Each component must be a "black box" with **clear Input/Output**.
- Separate concerns: **Agents** (orchestration), **Tools** (capabilities), **Prompts** (templates), **Memory** (state).
- Each component can be tested independently.

### Configurability
- **NEVER** hard-code parameters (model name, temperature, max_tokens, API endpoints, system prompts).
- All parameters must be read from config files (`config.json`, `config.yaml`, `.env`).
- Agent classes receive parameters via **Constructor Injection** or **Configuration Object**.
- Prompts should be externalized as templates, not embedded in code.

### Reusability
- Design tools, prompt templates, and utility functions that can be reused across agents.
- Separate agent orchestration logic from LLM provider-specific code.
- Use the **Strategy Pattern** for swappable LLM backends, embedding models, or vector stores.

### Simplicity Before Autonomy
- Start with the simplest architecture that can solve the problem: single-call prompt -> workflow -> agent loop.
- Prefer explicit workflows for predictable tasks and introduce autonomous agents only when fixed workflows are too rigid.
- Increase architectural complexity only when it measurably improves quality, reliability, or operator efficiency.

### Evaluation-Driven Development
- Design systems so output quality can be measured continuously, not judged only by intuition.
- Separate evaluation of sub-components when possible: retrieval, tool use, reasoning, and final response quality.
- Treat evaluation and debugging infrastructure as first-class system components, not optional tooling.

### Observability By Design
- Build tracing, structured logging, and metrics into the architecture from day one.
- Every agent step should have observable inputs, outputs, duration, and failure modes.
- Favor system designs that make intermediate states inspectable during debugging and review.

### Human Oversight And Control
- Keep explicit stopping conditions, approval points, or escalation paths for risky actions.
- Require stronger control boundaries for destructive actions, external side effects, or sensitive data access.
- Design agents so they can ask for clarification or defer to humans instead of improvising through ambiguity.
