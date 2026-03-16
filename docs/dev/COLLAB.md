# Human–AI Collaboration Guide for Software Development  

**Purpose**: Provide best practices for AI coding agents and human partners to build reliable, maintainable software together.  

---

## Core Principles  

1. **Consistency over Creativity** – Follow established patterns; avoid ad-hoc solutions.  
2. **Configuration First** – Make everything configurable with sensible defaults.  
3. **Hybrid Strategy** – Use the best available tools (libraries, external programs, APIs).  
4. **Graceful Failure** – Never let one error stop a workflow; always clean up resources.  
5. **Documentation Equals Power** – Record reasoning, tradeoffs, and decisions for future agents and humans.  

---

## Human vs AI Roles  

**Human Responsibilities**  
- Define requirements and goals.  
- Perform real-world testing.  
- Provide domain expertise and context.  
- Validate outputs and edge cases.  
- Review and refine AI-generated documentation.  

**AI Responsibilities**  
- Own technical implementation and architecture.  
- Ask clarifying questions about requirements, not code details.  
- Debug systematically, explain reasoning.  
- Maintain consistency across modules.  
- Generate and update documentation as part of every coding cycle.  

---

## Development Patterns  

1. **Prototype Fast, Then Refine**  
   - Build minimal working versions → validate with humans → improve.  
   - **Document**: Add “prototype notes” explaining assumptions and shortcuts.  

2. **Configuration-Driven Design**  
   - Load defaults, merge user config, avoid hardcoding.  
   - **Document**: Auto-generate config schema reference (default values, overrides).  

3. **Pattern Replication**  
   - Reuse proven structures for config, output, error handling, and testing.  
   - **Document**: Copy documentation patterns along with code patterns.  

4. **Systematic Debugging**  
   - Reproduce → isolate → research → test alternatives → implement → document.  
   - **Document**: Log root causes, failed attempts, and final solutions for future agents.  

---

## Collaboration Patterns  

- **Autonomous AI Decisions**: Don’t ask permission for standard technical choices.  
- **Human Validation Loop**: AI builds, human tests, AI fixes, repeat.  
- **Documentation as a Shared Tool**:  
  - AI: Generate inline docstrings, architecture notes, and changelogs.  
  - Human: Validate accuracy and fill real-world context.  
- **Red Flags**: Human micromanaging implementation, AI asking permission for routine code, skipped testing, quick hacks, missing docs.  

---

## Security & Ethics  

- Default to **read-only, safe operations**.  
- Add **rate limiting and timeouts** for respectful behavior.  
- Maintain **audit trails**: logs, manifests, reproducible outputs.  
- Emphasize **authorized use only** and responsible disclosure.  
- **Document**: Security constraints, ethical boundaries, and audit log formats.  

---

## User Experience Principles  

- **Streamlined by Default**: Sensible defaults, minimal required flags.  
- **Progress Feedback**: Always show status for long tasks.  
- **Progressive Disclosure**: Simple interface for common use, advanced options for experts.  
- **Unified CLI Pattern** (or equivalent): one tool with subcommands, not many small scripts.  
- **Document**: Example commands for common vs advanced use cases.  

---

## Lessons Learned  

1. **Theoretical correctness ≠ practical functionality** – always test in real conditions.  
   - **Document** failures found in testing.  
2. **Error handling defines usability** – suppress noise, surface meaningful messages.  
   - **Document** expected error behaviors and fallback logic.  
3. **Consistency accelerates maintenance** – identical patterns reduce bugs.  
   - **Document** consistency standards (naming, output, errors).  
4. **Documentation amplifies collaboration** – future agents rely on clear records.  
5. **Workflow testing > unit testing alone** – integration reveals hidden bugs.  
   - **Document** test plans and results.  

---

## Documentation Standards  

1. **Inline Code Documentation**  
   - Use docstrings for every function/class (purpose, params, return values).  
   - Update docstrings when functionality changes.  

2. **Architecture Notes**  
   - Maintain an `ARCHITECTURE.md` explaining system design and reasoning.  
   - Update when adding/removing modules.  

3. **Change Logging**  
   - Keep a `CHANGELOG.md` with date, feature, rationale.  
   - Each bug fix must include cause + resolution.  

4. **Usage Examples**  
   - Provide runnable examples in `USAGE.md` or CLI help text.  
   - Update examples when defaults or flags change.  

5. **Maintenance Cycle**  
   - **AI**: Always generate/update docs alongside code changes.  
   - **Human**: Review for accuracy and add context.  

---

## Checklist for AI Agents  

- [ ] Use consistent config, output, error handling patterns.  
- [ ] No hardcoded values; defaults must exist.  
- [ ] Clean up all resources with try/finally.  
- [ ] Provide user feedback during long ops.  
- [ ] **Generate/Update docstrings for all code changes.**  
- [ ] **Update architecture or changelog docs when structure changes.**  
- [ ] **Provide usage examples for new features.**  
- [ ] Document architecture decisions + tradeoffs.  
- [ ] Test realistic workflows, not just functions.  

---

**Key Insight**: Effective human–AI development is a partnership. The AI codes and documents with consistency and autonomy; the human validates both code and documentation against reality.  

