---
name: locate
description: One-call code locator. Pass the issue's literal names; get the ranked definitions to edit with their current code verbatim, same-name twins, other mentions and the test command.
---

# locate

Run it once, as your first action, with `run_skill_script`:

    skill_name: locate
    file_path: scripts/locate.py
    args: ["name1", "name2", "an exact error message", ...]

Pass 2-6 literal strings copied from the issue: function, class, parameter,
option or env-var names, dotted paths, exception types, distinctive fragments
of error messages. Prefer rare, specific identifiers over common words.

The report contains:
- RANKED DEFINITIONS: `path:start-end qualname score [terms]`, best first.
- `=== path:a-b ... verbatim:` blocks: the current code of the top definitions,
  copied exactly from the file. Use it directly as `old_string` for `edit_file`.
- SAME-NAME DEFINITIONS: twins (sync/async, v1/v2, sibling classes) that may
  need the same change.
- OTHER MENTIONS: module-level code, exports and docs_src examples.
- TESTS / RUN: the most relevant test file and a pytest command.

If the top results look unrelated, run it once more with different, more
specific terms instead of searching by hand.
