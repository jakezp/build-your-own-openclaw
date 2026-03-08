# Implementation Workflow - Build Your Own OpenClaw

## Overview

This document defines the step-by-step process for implementing each tutorial step. Following this workflow ensures that tutorial code stays aligned with picklebot patterns and avoids unnecessary code drift.

### Core Principles

1. **Copy from picklebot first** - Use battle-tested code as the foundation
2. **Trim future code** - Remove pieces needed only in later steps
3. **Keep it working** - Every step must be runnable

---

## Step Implementation Workflow

### Phase 1: Planning (Before Writing Code)

1. **Identify Required Files**
   - Read PLAN.md step description
   - List all files to be created/modified
   - Map each file to picklebot source: `../pickle-bot/src/picklebot/...`

2. **Categorize Each File**
   - **Type A**: New file (doesn't exist in previous step)
   - **Type B**: Existing file, no new features needed
   - **Type C**: Existing file, new features needed

### Phase 2: Implementation (File by File)

**For Type A (New file):**
1. Copy from picklebot → trim to essentials
2. Remove production complexity, keep core logic

**For Type B (Existing, no changes):**
1. Copy from previous step (no changes needed)
2. Verify it still works with new dependencies

**For Type C (Existing, needs changes):**
1. Copy from picklebot → temp file
2. Compare with previous step version
3. Identify what's new in picklebot version
4. Trim picklebot version to include ONLY new features

### Phase 3: Validation

1. Code runs: `uv run your-own-bot chat`
2. Test the new feature
3. Verify patterns match picklebot
4. Write/update README.md

---

## How to Trim Picklebot Code

### What to KEEP (Needed for THIS Step)
- ✅ Core logic required for current step's feature
- ✅ Essential dependencies and imports
- ✅ Error handling that affects current functionality
- ✅ Logging/retries/metrics if they're actually used

### What to REMOVE (Future Step Code)
- ❌ Code only needed in later tutorial steps
- ❌ Imports for features not yet implemented
- ❌ Configuration options for future features
- ❌ Helper functions for future capabilities
- ❌ "Forward-looking" abstractions

### Trimming Strategy

1. **Check PLAN.md** - What does THIS step need?
2. **Read picklebot file** - Identify which parts serve this step
3. **Remove future pieces** - Anything not needed until Step X
4. **Keep it working** - Don't break current functionality

### Example

**Step 01 (Tools) trimming:**
- ✅ Keep: ToolRegistry, basic tools (read/write/bash)
- ❌ Remove: SkillLoader (needed in Step 02)
- ❌ Remove: DispatchEvent handling (needed in Step 13)
- ❌ Remove: Concurrency control (needed in Step 16)

---

## Common Scenarios

### Scenario 1: "Picklebot file is very different from previous step"

**When:** Major refactoring happened in picklebot (e.g., Step 08 event-driven refactor)

**Solution:**
1. Use picklebot version as base (it's the target architecture)
2. Add back ONLY the features from previous step
3. Document the refactor in README.md

### Scenario 2: "Not sure which picklebot file to use"

**When:** Multiple files in picklebot seem relevant

**Solution:**
1. Check PLAN.md - it usually specifies the exact file
2. Look for similar naming in picklebot structure
3. When in doubt, ask or pick the simpler one

### Scenario 3: "Picklebot doesn't have this file"

**When:** Tutorial needs a file that doesn't exist in picklebot

**Solution:**
1. Double-check PLAN.md - are we sure this file is needed?
2. Check if similar functionality exists under different name
3. Only then: write new code (document why in README.md)

### Scenario 4: "Previous step code looks wrong"

**When:** Previous implementation drifted from picklebot patterns

**Solution:**
1. Trust picklebot over previous step
2. Replace with trimmed picklebot version
3. Note the correction in commit message

---

## Quick Reference: Implementing a Step

### Before You Start
- [ ] Read PLAN.md step description
- [ ] List all files needed
- [ ] Map files to picklebot sources

### For Each File
- [ ] Determine type: A (new) / B (no changes) / C (needs changes)
- [ ] **Type A**: Copy from picklebot → trim future code
- [ ] **Type B**: Copy from previous step
- [ ] **Type C**: Copy from picklebot → trim → merge new features only

### Trimming Checklist
- [ ] Keep only what's needed for THIS step
- [ ] Remove code for future steps
- [ ] Remove unused imports
- [ ] Keep core logic intact

### Validation Checklist
- [ ] `uv run your-own-bot chat` works
- [ ] New feature works as described
- [ ] Matches picklebot patterns
- [ ] README.md updated
- [ ] Commit with clear message

### File Priority Order
1. ⭐ picklebot code (trim to essentials)
2. ⭐ previous step code (if no changes needed)
3. ⭐ new code (only if no reference exists)

---

## How This Connects to PLAN.md

**PLAN.md** tells you **WHAT** to build:
- Which features each step needs
- Which picklebot files to reference
- What the end result should look like

**WORKFLOW.md** tells you **HOW** to build it:
- The process for implementing each file
- How to trim picklebot code
- How to validate your work

### Usage Pattern

1. Read PLAN.md → Understand the step's requirements
2. Follow WORKFLOW.md → Implement file by file
3. Return to PLAN.md → Verify you met the requirements

### Updates to PLAN.md

PLAN.md will reference this workflow in its "Implementation Principles" section:

```markdown
## Implementation Principles

### **CRITICAL: Follow the Workflow**

See [WORKFLOW.md](./WORKFLOW.md) for the step-by-step implementation process.
This ensures consistency with picklebot patterns across all steps.
```
