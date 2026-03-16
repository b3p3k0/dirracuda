# Documentation Guide for Human–AI Software Development  

**Purpose**: Establish lightweight, repeatable documentation practices for projects built by AI coding agents with human collaboration.  

---

## Core Principles  

1. **Docs are Code** – Generate and maintain documentation as part of every coding cycle.  
2. **Accuracy > Completeness** – Outdated docs are worse than missing ones.  
3. **Layered Documentation** – Provide quick references for common use and detailed notes for architecture and history.  
4. **Human + AI Roles** – AI generates and updates docs, human validates and adds context.  

---

## Documentation Types  

### 1. Inline Documentation  
- **Docstrings** for every function/class (purpose, params, returns, errors).  
- **Update** whenever functionality changes.  

### 2. Architecture Notes (`ARCHITECTURE.md`)  
- Explain system design, module responsibilities, and reasoning.  
- Update when adding/removing modules or changing data flow.  

### 3. Change Log (`CHANGELOG.md`)  
- Every change entry includes:  
  - **Date**  
  - **Feature or fix**  
  - **Reason or root cause**  
  - **Resolution/approach**  
- Serves as memory for future AI agents.  

### 4. Usage Guide (`USAGE.md`)  
- Simple runnable examples (common + advanced use).  
- Update when defaults, flags, or behaviors change.  

### 5. Security & Ethics Notes (`SECURITY.md`)  
- Summarize project safety constraints (read-only, rate limits, etc.).  
- Include explicit authorized-use disclaimer.  

---

## AI Responsibilities  

- Always generate/update **docstrings** for code changes.  
- Add/update entries in `CHANGELOG.md` when modifying features.  
- Update `ARCHITECTURE.md` when altering structure or design.  
- Provide or update examples in `USAGE.md` when adding features.  
- Document security/ethical boundaries when relevant.  

---

## Human Responsibilities  

- Review AI-generated docs for clarity and accuracy.  
- Add domain context, rationale, and real-world testing notes.  
- Ensure documentation reflects actual behavior in production.  

---

## Best Practices  

- **Small Updates**: Update docs in the same commit as code changes.  
- **Traceability**: Each bug fix includes documented cause and resolution.  
- **Consistency**: Follow identical patterns across all modules and projects.  
- **Review Cycle**: Humans validate docs during testing and before release.  

---

## Quick Checklist for AI Agents  

- [ ] Docstrings updated for all new/modified functions.  
- [ ] `CHANGELOG.md` entry added with date + rationale.  
- [ ] `ARCHITECTURE.md` updated if structure changed.  
- [ ] `USAGE.md` updated with examples for new/changed features.  
- [ ] `SECURITY.md` reviewed if new functionality affects safety/ethics.  

---

**Key Insight**: Documentation is not optional overhead. It is a core part of collaboration, enabling humans and future AI agents to maintain, extend, and trust the codebase.  

