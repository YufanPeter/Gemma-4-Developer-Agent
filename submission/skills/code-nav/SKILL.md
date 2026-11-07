---
name: code-nav
description: Search, read, edit and check code in the task repository. Set it up once with run_skill_script(skill_name="code-nav", file_path="scripts/nav.py", args=["setup"]). After that run every command with run_command, for example python3 /tmp/nav.py find NAME, python3 /tmp/nav.py show FILE SYMBOL, python3 /tmp/nav.py edit FILE START END <<'EOF' ... EOF, python3 /tmp/nav.py check.
---
# code-nav

Step 1, once per task: set it up with this exact call.

    run_skill_script(skill_name="code-nav", file_path="scripts/nav.py", args=["setup"])

Step 2: run every command with run_command, one command per call. Do not call run_skill_script again. If run_command says /tmp/nav.py does not exist, repeat step 1 once. Write names as plain words, without quotes or backticks.

Read (each command starts with python3 /tmp/nav.py):
- find NAME NAME ... : ranks source files and the functions inside them by 2-6 exact names or short strings from the issue. When the best function is short, its code is printed too.
- show FILE SYMBOL SYMBOL ... : up to 4 functions, classes, constants or class attributes (Class.method, NAME) with line numbers. A function longer than 60 lines is shown as its header plus the lines with your last find names; add --full for all of it.
- show FILE 120-180 : those lines (any text file). show FILE LINE: the function around LINE. show FILE: the outline of FILE.
- refs NAME : the uses of NAME outside tests, grouped by function. Use it to find every place a change must go.
- tests NAME : existing test functions that use NAME. They show the expected behaviour.

Before your first source edit you get 7 reads (only reads that found something count; after 3 minutes, 4 reads). Then reading pauses until your first source edit: a find, refs, tests or a show of new code does not run and prints the code you viewed last, with its line numbers, instead. A show of code you already viewed still works. Edit that code.

Edit: replace lines START-END of FILE with the lines between <<'EOF' and EOF. Write the new lines exactly as they must appear in the file, with their full indentation and normal quotes; nothing needs escaping. The closing EOF stands alone at the start of its line.

```
python3 /tmp/nav.py edit pkg/module.py 120 124 <<'EOF'
    if value is None:
        return default
    return value
EOF
```

- Insert without replacing: edit FILE after 124 <<'EOF' ... EOF. A new file: edit FILE after 0 <<'EOF' ... EOF.
- Remove lines: python3 /tmp/nav.py delete FILE 120 124
- A Python file that would not compile is not written: the output shows where it breaks; fix the lines and repeat the edit.
- The output shows the removed lines, the new lines with their numbers and how later line numbers moved, and places where the same change may also be needed. Use the new numbers for the next edit.

Check: python3 /tmp/nav.py check, once before submit_patch. It compiles the changed files, warns about changes the grader drops (tests, runner configs) and about new scratch files, lists places the same change may still be missing and the callers of the functions you changed, and prints a compact diff.

The first line of every output is your progress: code-nav calls so far, the time left of the 8 minutes, the files you have edited and the code you have already seen. Code marked "already shown in code-nav call N, unchanged since" is code you have: do not read it again.
Long output comes in pages; the last line is the command for the next page.
If any output starts with SYNTAX ERROR, your last edit broke that file: fix it first.
