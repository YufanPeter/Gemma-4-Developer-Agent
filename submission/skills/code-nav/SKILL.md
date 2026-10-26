---
name: code-nav
description: Read and check Python code in /workspace in one call - rank files and functions for issue terms, print a function with line numbers, list the uses of a name, show existing tests, and check the edits before submit_patch. Use instead of grep plus read_file.
---
Run with run_skill_script, skill_name code-nav, file_path scripts/nav.py, and args as a list of plain words. Paths are relative to /workspace. The script only reads; it never changes files.

- args ["find", TERM, TERM, ...]: ranks library source files, and the functions inside them, by how many of the terms they contain. Pass 2-6 exact names or short strings from the issue. Tests are skipped; matching docs_src, examples and scripts files are listed separately.
- args ["show", FILE, SYMBOL]: prints the function or class (Class.method or function name) with line numbers. Docstrings and Doc(...) texts are folded; other strings are shown in full. A function longer than 160 lines is printed in parts (the output says how to continue), and a large class as its member list. Use the printed line numbers for edits.
- args ["show", FILE, LINE]: prints the function that contains LINE.
- args ["refs", NAME]: the uses of NAME outside tests (docs_src and examples included), grouped by the function that contains it; very long lists are cut and say how many places were left out. Use it to find the places a change must also be made, and to follow an existing sibling parameter through the code.
- args ["tests", NAME, ...]: existing test functions that use NAME, with their first lines. Use them to see the expected behaviour when the issue is vague.
- args ["outline", FILE]: classes and functions of FILE with line ranges.
- args ["check"]: run once before submit_patch. Compiles the changed Python files, warns about changes the grader drops (tests, runner configs) and about new files named like scratch scripts, lists places the same change may still be missing, and prints a compact diff in which every changed, new or deleted file shows its first changes and how many lines were left out.

Output is capped at 6000 characters.
