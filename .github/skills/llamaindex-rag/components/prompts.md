# Prompts

## Concept

- Prompts are the fundamental input that drive LLM behavior in LlamaIndex.
- Used internally for: index construction, insertion, query traversal, and response synthesis.
- Default prompt templates are provided and work out of the box.
- Users can customize or replace any prompt.

---

## Template Types

### RichPromptTemplate (Recommended)

- Latest-style, Jinja2-based.
- Variables use double braces: `{{ variable }}`.
- Supports:
    - `{% chat role="..." %}` blocks to set message role in chat APIs.
    - `{% for ... %}` loops for iterating over dynamic content.
    - `{{ value | image }}` filter to embed image content blocks.
    - Conditional logic, object parsing.
- Methods:
    - `format(**kwargs)` returns a plain string (for completion API).
    - `format_messages(**kwargs)` returns a list of chat messages (for chat API).

### PromptTemplate

- Older-style, Python f-string based.
- Variables use single braces: `{variable}`.
- Same interface: `format()` and `format_messages()`.

### ChatPromptTemplate

- Older-style, built from a list of `ChatMessage` objects with roles.
- Same interface: `format()` and `format_messages()`.

---

## Advanced Capabilities

### Function Mappings

- Pass callables as template variable values instead of fixed strings.
- Enables dynamic behavior such as reformatting context, building few-shot examples at runtime.
- Specify via `function_mappings={"variable_name": callable}` in `RichPromptTemplate`.

### Partial Formatting

- Fill in some variables now and leave others for later.
- Use `template.partial_format(foo="abc")` to get a partially-formatted template.

### Template Variable Mappings

- Rename template variables to match LlamaIndex's expected keys without rewriting the template.
- Specify via `template_var_mappings={"context_str": "my_context", "query_str": "my_query"}`.

---

## Commonly Used Prompts

- `text_qa_template`: used to generate the initial answer from retrieved nodes.
- `refine_template`: used to refine an existing answer with additional context chunks.

---

## Accessing and Updating Prompts

- `module.get_prompts()`: returns a flat dict of all prompts used in the module and its sub-modules. Keys are namespaced (e.g. `response_synthesizer:text_qa_template`).
- `module.update_prompts({"key": new_template})`: replace a prompt by its key.

### Override at Query Engine Level

- High-level API: `index.as_query_engine(text_qa_template=..., refine_template=...)`.
- Low-level API: pass custom templates when constructing `get_response_synthesizer(...)` and `RetrieverQueryEngine(...)`.

---

## Prompt Injection Notes

- When receiving prompts from untrusted inputs, ensure user-provided values are clearly separated from instructions.
- LlamaIndex default prompts use structured templates; custom prompts should follow the same discipline.
