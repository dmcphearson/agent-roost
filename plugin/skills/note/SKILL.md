---
name: note
description: Save an idea or a note to the person's roost Notes, their scratchpad, to come back to later. Use when the person runs /note, or asks to save, jot down, park, or note something for later.
argument-hint: "[what to save]"
---

# Save a note

What to save: `$ARGUMENTS` (if that's empty, it's what the person just asked you to save; if it's still unclear, ask them).

1. **Title:** a few words that will make sense weeks from now, like "Printer driver idea".
2. **Text:** the idea in the person's words, then only the context from this Session needed to understand it later (what prompted it, a file or link, an open question). A few lines of Markdown. Leave out secrets.
3. **Save it:**

   ```
   roost note "<title>" <<'EOF'
   <text>
   EOF
   ```

   It prints `Saved <path>`. Tell the person the title and that it's on roost's Notes tab. If it fails, show them the error.

A note is the person's scratchpad, not project work: don't add it to a project or a Handoff, and don't edit notes that are already there.
