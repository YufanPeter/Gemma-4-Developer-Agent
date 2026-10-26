---
name: code-nav
description: Search, read and check Python code in the task repository. Install once with run_skill_script (args ["install"]), then run python3 /tmp/nav.py COMMAND with run_command - find, show, refs, tests, outline, check. Use instead of grep plus read_file.
---
Install once:

    run_skill_script(skill_name="code-nav", file_path="scripts/nav.py", args=["install"])

After that, run every command with run_command from the repository, for example
`run_command(command="python3 /tmp/nav.py find exact_name other_name")`. The script only reads; it never changes files.

- `python3 /tmp/nav.py find TERM ...`: ranks library source files, and the functions inside them, by how many of the terms they contain. Pass 2-6 exact names or short strings from the issue. Tests are skipped; matching docs_src, examples and scripts files are listed separately.
- `python3 /tmp/nav.py show FILE SYMBOL`: prints the function or class (Class.method or function name) with line numbers. Docstrings and Doc(...) texts are folded; other strings are shown in full. A function longer than 160 lines is printed in parts (the output says how to continue), and a large class as its member list.
- `python3 /tmp/nav.py show FILE LINE`: prints the function that contains LINE.
- `python3 /tmp/nav.py refs NAME`: the uses of NAME outside tests (docs_src and examples included), grouped by the function that contains it; very long lists are cut and say how many places were left out.
- `python3 /tmp/nav.py tests NAME ...`: existing test functions that use NAME, with their first lines.
- `python3 /tmp/nav.py outline FILE`: classes and functions of FILE with line ranges.
- `python3 /tmp/nav.py check`: run once before submit_patch. Compiles the changed Python files, warns about changes the grader drops (tests, runner configs) and about new files named like scratch scripts, lists places the same change may still be missing, and prints a compact diff in which every changed, new or deleted file shows its first changes.

Output is capped at 6000 characters.
