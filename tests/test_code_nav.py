"""Regression tests for submission/skills/code-nav/scripts/nav.py (stdlib only; needs git).

Run from the repo root:  python3 -m unittest tests.test_code_nav -v
Each test builds a small git repo in a temp dir and runs nav.py on it with NAV_ROOT.
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

NAV = os.environ.get("NAV_SCRIPT") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "submission", "skills", "code-nav", "scripts", "nav.py")


def load_nav():
    sys.dont_write_bytecode = True  # never leave __pycache__ inside submission/
    spec = importlib.util.spec_from_file_location("nav_under_test", NAV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


nav = load_nav()


class Repo:
    """A throwaway git repo with a committed baseline."""

    def __init__(self, files):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        for path, text in files.items():
            self.write(path, text)
        self.git("init", "-q")
        self.git("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        self.git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "baseline")

    def write(self, path, text):
        full = os.path.join(self.root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write(textwrap.dedent(text))

    def remove(self, path):
        os.remove(os.path.join(self.root, path))

    def git(self, *args):
        subprocess.run(["git"] + list(args), cwd=self.root, check=True, capture_output=True)

    def nav(self, *args, cwd=None, use_env=True):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        env.pop("NAV_ROOT", None)
        if use_env:
            env["NAV_ROOT"] = self.root
        res = subprocess.run([sys.executable, "-I", NAV] + list(args), cwd=cwd or tempfile.gettempdir(),
                             env=env, capture_output=True, text=True, timeout=60)
        if res.returncode != 0 or "Traceback" in res.stderr:
            raise AssertionError("nav.py %s failed:\n%s" % (" ".join(args), res.stderr))
        return res.stdout

    def close(self):
        self.tmp.cleanup()


CORE = '''\
    """Core module."""
    CONSOLE_HTML_FORMAT = """\\
    <html>
    <body>{code}</body>
    </html>
    """


    def check(value):
        """Validate value.

        Longer docstring line one.
        Longer docstring line two.
        """
        if value < 0:
            raise ValueError(
                "value must be non-negative, "
                "got a negative number"
            )
        register(
            "important_option",
            "critical_default",
        )
        return value


    class App:
        def run(self, title, version):
            return build(title=title, version=version, debug=False)


    def build(title, version, debug):
        return title, version, debug


    def quick():
        return build("t", "v", True)
'''


class TestClassifiers(unittest.TestCase):
    def test_scratch_names(self):
        nav.ROOT = "/nonexistent"
        for path in ["module.py", "src/template.py", "src/debugger.py", "src/attempt.py",
                     "pkg/contemporary.py", "setup.py", "examples/demo.py", "notes.txt"]:
            self.assertFalse(nav.looks_like_scratch(path), path)
        for path in ["repro.py", "reproduce_issue.py", "tmp_check.py", "scratch.py", "scripts/debug_run.py"]:
            self.assertTrue(nav.looks_like_scratch(path), path)

    def test_scratch_name_inside_package_is_kept(self):
        repo = Repo({"pkg/__init__.py": "", "pkg/core.py": "x = 1\n"})
        try:
            nav.ROOT = repo.root
            self.assertFalse(nav.looks_like_scratch("pkg/tmp_utils.py"))
        finally:
            repo.close()

    def test_protected_matches_grader(self):
        for path in ["pyproject.toml", "pkg/conftest.py", "setup.cfg", "tox.ini", "sitecustomize.py",
                     "usercustomize.py", "_swegemma_stubs.py", "x.pth", "Tests/helper.py",
                     "src/pkg/testing/util.py", "docs_src/test_tutorial.py", "pkg/core_test.py"]:
            self.assertTrue(nav.is_protected(path), path)
        for path in ["pkg/core.py", "tests/data/x.json", "docs_src/tutorial.py", "README.md"]:
            self.assertFalse(nav.is_protected(path), path)


class TestShow(unittest.TestCase):
    def setUp(self):
        self.repo = Repo({"pkg/__init__.py": "", "pkg/core.py": CORE})

    def tearDown(self):
        self.repo.close()

    def test_docstring_folded_but_messages_visible(self):
        out = self.repo.nav("show", "pkg/core.py", "check")
        self.assertIn("lines of text folded", out)
        self.assertNotIn("Longer docstring line one", out)
        self.assertIn("value must be non-negative", out)
        self.assertIn("important_option", out)

    def test_module_template_visible(self):
        out = self.repo.nav("show", "pkg/core.py", "3")
        self.assertIn("<body>{code}</body>", out)

    def test_doc_annotation_folded(self):
        self.repo.write("pkg/api.py", '''\
            def make(
                title: Annotated[
                    str,
                    Doc(
                        """
                        The title.
                        """
                    ),
                ] = "x",
            ):
                return title
            ''')
        out = self.repo.nav("show", "pkg/api.py", "make")
        self.assertIn("Doc(...)", out)
        self.assertNotIn("The title.", out)

    def test_method_and_ambiguity(self):
        self.assertIn("App.run", self.repo.nav("show", "pkg/core.py", "run"))
        self.assertIn("not found", self.repo.nav("show", "pkg/core.py", "nosuch"))

    def test_paths_outside_workspace_refused(self):
        self.assertIn("outside the repository", self.repo.nav("outline", "/etc/passwd"))
        self.assertIn("outside the repository", self.repo.nav("show", "../" * 30 + "etc/hosts", "1"))
        self.assertIn("check", self.repo.nav("show", "./pkg/core.py", "check"))
        self.assertIn("check", self.repo.nav("show", os.path.join(self.repo.root, "pkg/core.py"), "check"))


class TestSearch(unittest.TestCase):
    def setUp(self):
        self.repo = Repo({
            "pkg/__init__.py": "",
            "pkg/core.py": CORE,
            "docs_src/tutorial001.py": "from pkg.core import build\n\n\ndef demo_build():\n    return build(title='t', version='v', debug=False)\n",
            "tests/test_core.py": "from pkg.core import check\n\n\ndef test_check_ok():\n    assert check(1) == 1\n",
        })

    def tearDown(self):
        self.repo.close()

    def test_find_ranks_library_and_lists_docs_apart(self):
        out = self.repo.nav("find", "build", "version")
        self.assertTrue(out.startswith("pkg/core.py"), out)
        self.assertIn("docs/examples/scripts code", out)
        self.assertIn("docs_src/tutorial001.py  demo_build L4-5", out)
        self.assertNotIn("tests/test_core.py", out)

    def test_refs_includes_docs_excludes_tests(self):
        out = self.repo.nav("refs", "build")
        self.assertIn("docs_src/tutorial001.py", out)
        self.assertIn("App.run", out)
        self.assertNotIn("tests/", out)

    def test_tests_command(self):
        out = self.repo.nav("tests", "check")
        self.assertIn("tests/test_core.py::test_check_ok", out)

    def test_dict_and_string_args(self):
        self.assertIn("App.run", self.repo.nav("show pkg/core.py App.run"))
        self.assertIn("App.run", self.repo.nav("--show", "pkg/core.py", "--symbol", "App.run"))
        self.assertIn("pkg/core.py  check", self.repo.nav("show", "pkg/core.py:16"))

    def test_cli_flag_terms_kept(self):
        self.repo.write("pkg/cli.py", "OPTIONS = ['--no-color', '--force']\n")
        self.repo.git("add", "pkg/cli.py")
        self.assertIn("pkg/cli.py", self.repo.nav("find", "--no-color"))
        self.assertIn("pkg/cli.py", self.repo.nav("refs", "--force"))


class TestCheck(unittest.TestCase):
    def setUp(self):
        self.repo = Repo({
            "pkg/__init__.py": "",
            "pkg/core.py": CORE,
            "pkg/warn.py": "import warnings\n\n\ndef a():\n    warnings.warn('a', category=DeprecationWarning)\n\n\n"
                           "def b():\n    warnings.warn('b', category=DeprecationWarning)\n\n\n"
                           "def c():\n    warnings.warn('c', category=DeprecationWarning)\n",
            "old_module.py": "x = 1\n",
        })

    def tearDown(self):
        self.repo.close()

    def test_no_changes(self):
        self.assertIn("NO CHANGES", self.repo.nav("check"))

    def test_deleted_and_new_files_in_diff(self):
        self.repo.remove("old_module.py")
        self.repo.write("docs_src/feature/tutorial001.py", "from pkg.core import check\n\napp = check(1)\n")
        out = self.repo.nav("check")
        self.assertIn("old_module.py (deleted)", out)
        self.assertIn("-    1 x = 1", out)
        self.assertIn("docs_src/feature/tutorial001.py (new file, 3 lines)", out)
        self.assertNotIn("scratch script", out)

    def test_intent_to_add_file_labelled_new(self):
        self.repo.write("pkg/extra.py", "y = 2\n")
        self.repo.git("add", "-N", "pkg/extra.py")
        self.assertIn("pkg/extra.py (new file)", self.repo.nav("check"))

    def test_scratch_and_protected_warnings(self):
        self.repo.write("repro.py", "print(1)\n")
        self.repo.write("module.py", "VALUE = 1\n")
        self.repo.write("pyproject.toml", "[tool.x]\n")
        out = self.repo.nav("check")
        self.assertIn("new file repro.py is named like a scratch script", out)
        self.assertNotIn("module.py is named like", out)
        self.assertIn("protected file changed: pyproject.toml", out)

    def test_only_protected_python_change_is_flagged(self):
        self.repo.write("sitecustomize.py", "import os\n")
        out = self.repo.nav("check")
        self.assertIn("protected file changed: sitecustomize.py", out)
        self.assertIn("no .py file outside tests and runner configs changed", out)

    def test_compile_error(self):
        self.repo.write("pkg/core.py", CORE.replace("return value", "return value)"))
        self.assertIn("compile: ERRORS", self.repo.nav("check"))

    def test_replaced_name_still_used_elsewhere(self):
        with open(os.path.join(self.repo.root, "pkg/warn.py")) as fh:
            text = fh.read()
        text = text.replace("'a', category=DeprecationWarning", "'a', category=PkgDeprecationWarning")
        text = text.replace("'b', category=DeprecationWarning", "'b', category=PkgDeprecationWarning")
        self.repo.write("pkg/warn.py", text)
        out = self.repo.nav("check")
        self.assertIn("DeprecationWarning: replaced on 2 edited line(s)", out)
        self.assertIn("pkg/warn.py  c", out)

    def test_single_local_refactor_not_flagged(self):
        self.repo.write("pkg/core.py", CORE.replace("return title, version, debug", "result = title, version, debug\n    return result"))
        self.assertIn("possible missed places: none found", self.repo.nav("check"))

    def test_required_parameter_flags_all_callers(self):
        self.repo.write("pkg/core.py", CORE.replace("def build(title, version, debug):", "def build(title, version, debug, mode):"))
        out = self.repo.nav("check")
        self.assertIn("build changed parameters (+mode (required)); calls in untouched places", out)
        self.assertIn("App.run", out)
        self.assertIn("pkg/core.py  quick", out)

    def test_optional_parameter_flags_only_forwarding_callers(self):
        # App.run passes title=, version=, debug= one by one, so it may need to pass mode too; quick() does not.
        self.repo.write("pkg/core.py", CORE.replace("def build(title, version, debug):", "def build(title, version, debug, mode=None):"))
        out = self.repo.nav("check")
        self.assertIn("build changed parameters (+mode); library calls that may need to pass it on", out)
        self.assertIn("App.run", out)
        self.assertNotIn("pkg/core.py  quick", out)

    def test_signature_rule_is_capped(self):
        funcs = "".join("def f%d(a):\n    return a\n\n\ndef use%d():\n    return f%d(1)\n\n\n" % (i, i, i) for i in range(9))
        self.repo.write("pkg/many.py", funcs)
        self.repo.git("add", "pkg/many.py")
        self.repo.git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "many")
        self.repo.write("pkg/many.py", funcs.replace("(a):", "(a, b):"))
        out = self.repo.nav("check")
        self.assertEqual(out.count("changed parameters"), 5)

    def test_every_file_appears_in_diff(self):
        big = "\n".join("LINE_%03d = %d" % (i, i) for i in range(300)) + "\n"
        self.repo.write("pkg/aaa_big.py", big)
        self.repo.write("pkg/core.py", CORE.replace("return value", "return value + 0"))
        self.repo.write("pkg/zzz_last.py", "LAST = 1\n")
        out = self.repo.nav("check")
        diff = out.split("diff (- old line numbers")[1]
        for name in ("pkg/aaa_big.py", "pkg/core.py", "pkg/zzz_last.py"):
            self.assertIn(name, diff)
        self.assertIn("more changed lines in this file", diff)
        self.assertIn("LAST = 1", diff)
        self.assertLess(len(out), 6100)


class TestRunCommandUse(unittest.TestCase):
    """How the agent runs it: run_command starts in the repository; no NAV_ROOT."""

    def setUp(self):
        self.repo = Repo({"pkg/__init__.py": "", "pkg/core.py": CORE})

    def tearDown(self):
        self.repo.close()

    def test_repo_found_from_current_directory(self):
        out = self.repo.nav("find", "build", "version", cwd=os.path.join(self.repo.root, "pkg"), use_env=False)
        self.assertTrue(out.startswith("pkg/core.py"), out)

    def test_outside_repo_prints_run_command_hint(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            out = self.repo.nav("find", "build", cwd=elsewhere, use_env=False)
        self.assertIn("Run it with run_command", out)
        self.assertIn("python3 /tmp/nav.py find build", out)

    def test_quotes_and_backticks_are_dropped(self):
        out = self.repo.nav('"find"', "`build`", "'version'")
        self.assertTrue(out.startswith("pkg/core.py"), out)


def swegemma_rewrite(command, workspace, tmp):
    """The /tmp and /workspace rewriting that swegemma's subprocess sandbox applies to run_command strings."""
    command = re.sub(r"(?<![\w.-])/tmp(?![\w.-])", tmp, command)
    return re.sub(r"(?<![\w.-])/workspace(?![\w.-])", workspace, command)


class TestSubprocessSandboxEmulation(unittest.TestCase):
    """Kaggle runs tasks in swegemma's subprocess sandbox: <root>/workspace holds the repo, <root>/tmp is
    what /tmp means inside run_command, and there is no real /workspace directory."""

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.workspace = os.path.join(self.box.name, "workspace")
        self.tmp = os.path.join(self.box.name, "tmp")
        os.makedirs(self.tmp)
        self.repo = Repo({"pkg/__init__.py": "", "pkg/core.py": CORE})
        shutil.copytree(self.repo.root, self.workspace)

    def tearDown(self):
        self.repo.close()
        self.box.cleanup()

    def run_skill_install(self, tmpdir):
        """Like ADK's run_skill_script: copy the script into a fresh temp dir and run it there."""
        stage = tempfile.mkdtemp(dir=tmpdir)
        os.makedirs(os.path.join(stage, "scripts"))
        shutil.copy(NAV, os.path.join(stage, "scripts", "nav.py"))
        code = ("import os, runpy, sys; os.chdir(%r); sys.argv = ['scripts/nav.py', 'install']; "
                "runpy.run_path('scripts/nav.py', run_name='__main__')" % stage)
        env = {k: v for k, v in os.environ.items() if k != "NAV_ROOT"}
        env.update(PYTHONPATH=self.workspace, TMPDIR=tmpdir, PYTHONDONTWRITEBYTECODE="1")
        res = subprocess.run([sys.executable, "-c", code], cwd=self.workspace, env=env,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        return res.stdout

    def run_command(self, command):
        env = {k: v for k, v in os.environ.items() if k != "NAV_ROOT"}
        env.update(PYTHONPATH=self.workspace, PYTHONDONTWRITEBYTECODE="1", PATH=os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", ""))
        res = subprocess.run(["/bin/bash", "-c", swegemma_rewrite(command, self.workspace, self.tmp)],
                             cwd=self.workspace, env=env, capture_output=True, text=True, timeout=60)
        return res.stdout + res.stderr

    def test_install_then_run_command(self):
        out = self.run_skill_install(self.tmp)
        self.assertIn("installed code-nav", out)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "nav.py")))
        found = self.run_command("python3 /tmp/nav.py find build version")
        self.assertTrue(found.startswith("pkg/core.py"), found)
        self.assertIn("App.run", self.run_command("python3 /tmp/nav.py show pkg/core.py App.run"))

    def test_install_reaches_sandbox_tmp_even_if_tmpdir_is_elsewhere(self):
        with tempfile.TemporaryDirectory() as host_tmp:
            self.run_skill_install(host_tmp)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "nav.py")))

    def test_check_sees_edits_through_run_command(self):
        self.run_skill_install(self.tmp)
        with open(os.path.join(self.workspace, "pkg", "core.py"), "a") as fh:
            fh.write("\nEXTRA = 1\n")
        out = self.run_command("python3 /tmp/nav.py check")
        self.assertNotIn("NO CHANGES", out)
        self.assertIn("pkg/core.py", out)

    def test_old_style_skill_call_explains_run_command(self):
        stage = tempfile.mkdtemp(dir=self.tmp)
        shutil.copy(NAV, os.path.join(stage, "nav.py"))
        env = {k: v for k, v in os.environ.items() if k != "NAV_ROOT"}
        env.update(PYTHONPATH=self.workspace, PYTHONDONTWRITEBYTECODE="1")
        res = subprocess.run([sys.executable, os.path.join(stage, "nav.py"), "find", "build"], cwd=stage,
                             env=env, capture_output=True, text=True, timeout=60)
        self.assertIn("Run it with run_command", res.stdout)


if __name__ == "__main__":
    unittest.main()
