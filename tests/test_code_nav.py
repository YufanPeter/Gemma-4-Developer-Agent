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
        self.state = tempfile.TemporaryDirectory()  # stands in for /tmp: holds nav.py and nav_state.json
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

    def nav(self, *args, cwd=None, use_env=True, raw=False):
        """Run the set-up copy (/tmp/nav.py on Kaggle) as run_command would; status lines dropped unless raw."""
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", NAV_STATE_DIR=self.state.name)
        env.pop("NAV_ROOT", None)
        if use_env:
            env["NAV_ROOT"] = self.root
        installed = os.path.join(self.state.name, "nav.py")
        shutil.copy(NAV, installed)
        res = subprocess.run([sys.executable, "-I", installed] + list(args), cwd=cwd or tempfile.gettempdir(),
                             env=env, capture_output=True, text=True, timeout=60)
        if res.returncode != 0 or "Traceback" in res.stderr:
            raise AssertionError("nav.py %s failed:\n%s" % (" ".join(args), res.stderr))
        return res.stdout if raw else body(res.stdout)

    def close(self):
        self.tmp.cleanup()
        self.state.cleanup()


STATUS_PREFIXES = ("[code-nav call", "!! No source edit", "code-nav is ready. Run the next")


def body(text):
    """Output without the status lines that code-nav prints first."""
    lines = text.split("\n")
    while lines and lines[0].startswith(STATUS_PREFIXES):
        lines.pop(0)
    return "\n".join(lines)


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
        self.assertIn("not defined", self.repo.nav("show", "pkg/core.py", "nosuch"))

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
        missed = out.split("possible missed places")[1].split("callers of the functions you changed")[0]
        self.assertNotIn("pkg/core.py  quick", missed)
        self.assertIn("pkg/core.py  quick  L36: calls build", out)  # only as a caller, the weaker section

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



APP = """\
from pkg.router import Router


class App:
    def __init__(self, title, debug=False):
        self.router = Router()

    def get(self, path, response_model=None, status_code=200, tags=None):
        return self.add_route(path, response_model=response_model, status_code=status_code, tags=tags)

    def add_route(self, path, response_model=None, status_code=200, tags=None):
        value = self.router.add_route(path, response_model=response_model, status_code=status_code, tags=tags)
        return value
"""

ROUTER = """\
from pkg.util import _legacy_encode


class Router:
    def add_route(self, path, response_model=None, status_code=200, tags=None):
        self.routes = [path, response_model, status_code, tags]
        payload = _legacy_encode(response_model, status_code)
        return payload

    def websocket(self, path, name=None):
        encoded_value = _legacy_encode(name, None)
        return encoded_value
"""

UTIL = """\
def _legacy_encode(model, code):
    return [model, code]


def _modern_encode(model, code):
    return (model, code)
"""


class TestRelatedPlaces(unittest.TestCase):
    """check's rules that use how the code was changed, then the callers of the changed functions."""

    def setUp(self):
        self.repo = Repo({"pkg/__init__.py": "", "pkg/app.py": APP, "pkg/router.py": ROUTER, "pkg/util.py": UTIL,
                          "tests/test_app.py": "def test_x():\n    pass\n"})

    def tearDown(self):
        self.repo.close()

    def edit(self, path, old, new):
        with open(os.path.join(self.repo.root, path)) as fh:
            text = fh.read()
        self.assertIn(old, text)
        self.repo.write(path, text.replace(old, new))

    def test_new_parameter_lists_siblings_that_lack_it(self):
        self.edit("pkg/app.py", "    def add_route(self, path, response_model=None, status_code=200, tags=None):",
                  "    def add_route(self, path, response_model=None, status_code=200, tags=None, strict=False):")
        out = self.repo.nav("check")
        self.assertIn("same kind of code elsewhere:", out)
        self.assertIn("pkg/router.py  Router.add_route  L5: shares 4 parameters with App.add_route but has no strict", out)
        self.assertIn("pkg/app.py  App.get", out)
        self.assertNotIn("Router.websocket  L", out.split("callers of the functions")[0])

    def test_copied_line_and_removed_name(self):
        self.edit("pkg/router.py", "        payload = _legacy_encode(response_model, status_code)",
                  "        payload = _modern_encode(response_model, status_code)")
        self.edit("pkg/router.py", "from pkg.util import _legacy_encode", "from pkg.util import _legacy_encode, _modern_encode")
        out = self.repo.nav("check")
        self.assertIn("Router.websocket", out)
        self.assertIn("_legacy_encode", out)

    def test_body_change_lists_callers_in_their_own_section(self):
        self.edit("pkg/app.py", "        return value\n", "        return value or None\n")
        out = self.repo.nav("check")
        self.assertIn("possible missed places: none found", out)
        self.assertIn("callers of the functions you changed", out)
        self.assertIn("pkg/app.py  App.get  L8: calls App.add_route", out)
        self.assertNotIn("tests/", out.split("diff (- old")[0])

    def test_related_lines_are_capped(self):
        many = "".join("def caller%d(path, response_model=None, status_code=200, tags=None):\n"
                       "    return Router().add_route(path, response_model, status_code, tags)\n\n\n" % i for i in range(20))
        self.repo.write("pkg/many.py", "from pkg.router import Router\n\n\n" + many)
        self.repo.git("add", "pkg/many.py")
        self.repo.git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "many")
        self.edit("pkg/router.py", "        self.routes = [path, response_model, status_code, tags]",
                  "        self.routes = [path, response_model, status_code, tags, None]")
        out = self.repo.nav("check")
        head = out.split("diff (- old")[0]
        self.assertLessEqual(len(re.findall(r"  L\d+: ", head)), 8)
        self.assertLess(len(out), 4500)


class TestRunCommandUse(unittest.TestCase):
    """How the agent runs it: run_command starts in the repository; no NAV_ROOT."""

    def setUp(self):
        self.repo = Repo({"pkg/__init__.py": "", "pkg/core.py": CORE})

    def tearDown(self):
        self.repo.close()

    def test_repo_found_from_current_directory(self):
        out = self.repo.nav("find", "build", "version", cwd=os.path.join(self.repo.root, "pkg"), use_env=False)
        self.assertTrue(out.startswith("pkg/core.py"), out)

    def test_outside_repo_says_so(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            out = self.repo.nav("find", "build", cwd=elsewhere, use_env=False)
        self.assertIn("could not find the repository", out)

    def test_quotes_and_backticks_are_dropped(self):
        out = self.repo.nav('"find"', "`build`", "'version'")
        self.assertTrue(out.startswith("pkg/core.py"), out)


class TestSubprocessSandboxEmulation(unittest.TestCase):
    """Kaggle runs tasks in swegemma's subprocess sandbox: <root>/workspace holds the repo, PYTHONPATH points
    at it, <root>/tmp is what /tmp means inside run_command, and there is no real /workspace. ADK's
    run_skill_script copies the skill into a temp dir, chdirs there and runs scripts/nav.py with the args.
    The setup call copies nav.py to <root>/tmp; later commands run it there through run_command."""

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

    def env(self):
        env = {k: v for k, v in os.environ.items() if k not in ("NAV_ROOT", "NAV_STATE_DIR")}
        env.update(PYTHONPATH=self.workspace, TMPDIR=self.tmp, PYTHONDONTWRITEBYTECODE="1")
        return env

    def skill(self, *args):
        """Like ADK's run_skill_script: materialize the skill in a fresh temp dir and run it there."""
        stage = tempfile.mkdtemp(dir=self.tmp)
        os.makedirs(os.path.join(stage, "scripts"))
        shutil.copy(NAV, os.path.join(stage, "scripts", "nav.py"))
        code = ("import os, runpy, sys; os.chdir(%r); sys.argv = ['scripts/nav.py'] + %r; "
                "runpy.run_path('scripts/nav.py', run_name='__main__')" % (stage, list(args)))
        res = subprocess.run([sys.executable, "-c", code], cwd=self.workspace, env=self.env(),
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        return res.stdout

    def run_command(self, command):
        """Like swegemma's run_command: /tmp in the command means <root>/tmp; cwd is the workspace."""
        res = subprocess.run(command.replace("/tmp/", self.tmp + "/"), shell=True, cwd=self.workspace,
                             env=self.env(), capture_output=True, text=True, timeout=60)
        self.assertNotIn("Traceback", res.stderr)
        return res.stdout + res.stderr

    def test_setup_then_run_command(self):
        for args in ((), ("setup",), ("install",), ('"setup"',), ("`setup`",)):
            out = self.skill(*args)
            self.assertIn("code-nav is ready", out, args)
            self.assertIn("python3 /tmp/nav.py find", out, args)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "nav.py")))
        out = self.run_command("python3 /tmp/nav.py find build version")
        self.assertNotIn("code-nav is ready", out)
        self.assertTrue(body(out).startswith("pkg/core.py"), out)
        self.assertIn("App.run", body(self.run_command("python3 /tmp/nav.py show pkg/core.py App.run")))
        self.assertIn("already set up", self.run_command("python3 /tmp/nav.py setup"))

    def test_first_skill_call_with_a_command_sets_up_and_answers(self):
        out = self.skill("find build version")
        self.assertTrue(out.startswith("code-nav is ready. Run the next commands with run_command"), out)
        self.assertTrue(body(out).startswith("pkg/core.py"), out)
        self.assertIn("App.run", body(self.skill("show", "pkg/core.py", "App.run")))  # list form also works
        self.assertIn("App.run", body(self.run_command("python3 /tmp/nav.py show pkg/core.py App.run")))

    def test_state_is_shared_between_setup_and_run_command(self):
        self.skill("setup")
        self.run_command("python3 /tmp/nav.py show pkg/core.py check")
        out = self.run_command("python3 /tmp/nav.py show pkg/core.py check")
        self.assertIn("[code-nav call 2", out)
        self.assertIn("already shown in code-nav call 1", out)

    def test_check_sees_edits(self):
        self.skill("setup")
        with open(os.path.join(self.workspace, "pkg", "core.py"), "a") as fh:
            fh.write("\nEXTRA = 1\n")
        out = self.run_command("python3 /tmp/nav.py check")
        self.assertNotIn("NO CHANGES", out)
        self.assertIn("edited: pkg/core.py +2 -0", out)

    def test_quoted_and_backticked_words(self):
        self.assertTrue(body(self.skill("`find` `build`")).startswith("pkg/core.py"))
        self.assertTrue(body(self.run_command("python3 /tmp/nav.py '\"find\"' '\"build\"'")).startswith("pkg/core.py"))

    def test_syntax_banner(self):
        self.skill("setup")
        with open(os.path.join(self.workspace, "pkg", "core.py"), "a") as fh:
            fh.write("\ndef broken(:\n")
        out = body(self.run_command("python3 /tmp/nav.py find build"))
        self.assertTrue(out.startswith("SYNTAX ERROR"), out)
        self.assertIn("pkg/core.py:", out)

    def test_no_repo_on_pythonpath_says_so(self):
        stage = tempfile.mkdtemp(dir=self.tmp)
        shutil.copy(NAV, os.path.join(stage, "nav.py"))
        env = {k: v for k, v in os.environ.items() if k not in ("NAV_ROOT", "PYTHONPATH", "NAV_STATE_DIR")}
        env.update(PYTHONDONTWRITEBYTECODE="1", TMPDIR=stage)
        res = subprocess.run([sys.executable, os.path.join(stage, "nav.py"), "find", "build"], cwd=stage,
                             env=env, capture_output=True, text=True, timeout=60)
        self.assertIn("could not find the repository", res.stdout)


LONG = "def long_function(x):\n" + "".join("    value_%03d = x + %d  # some padding text to make the line longer\n" % (i, i) for i in range(300)) + "    return x\n"
NESTED = '''\
import sys
MAX_SIZE = 100
from os.path import join

if sys.version_info >= (3, 8):
    def compat_func(x):
        return x
else:
    def compat_func(x):
        return -x

try:
    def tried():
        return 1
except ImportError:
    pass


class Box:
    LIMIT = 5

    @property
    def plain(self):
        return 1

    @plain.setter
    def plain(self, v):
        pass
'''


class TestPagingAndNavigation(unittest.TestCase):
    def setUp(self):
        self.repo = Repo({"pkg/__init__.py": "", "pkg/core.py": CORE, "pkg/long.py": LONG, "pkg/nested.py": NESTED})

    def tearDown(self):
        self.repo.close()

    def test_long_output_stays_under_limit_with_next_page(self):
        out = self.repo.nav("show", "pkg/long.py", "long_function", raw=True)
        self.assertLess(len(out), 4500)
        last = out.strip().splitlines()[-1]
        self.assertIn("Next page: python3 /tmp/nav.py show pkg/long.py ", last)
        rng = re.search(r"show pkg/long.py (\S+)$", last).group(1)
        nxt = self.repo.nav("show", "pkg/long.py", rng, raw=True)
        self.assertLess(len(nxt), 4500)
        self.assertIn("lines %s" % rng, nxt)

    def test_every_command_stays_under_limit(self):
        self.repo.write("pkg/many.py", "".join("def f%d():\n    return build\n" % i for i in range(400)))
        self.repo.git("add", "pkg/many.py")
        for args in (("refs", "build"), ("show", "pkg/many.py"), ("find", "build", "return"),
                     ("show", "pkg/long.py", "long_function", "pkg/core.py")):
            self.assertLess(len(self.repo.nav(*args, raw=True)), 4500, args)

    def test_line_ranges(self):
        for args in (("pkg/nested.py", "6-10"), ("pkg/nested.py:6-10",), ("pkg/nested.py", "6", "-", "10"),
                     ("pkg/nested.py", "L6-L10"), ("pkg/nested.py", "6", "10")):
            out = self.repo.nav("show", *args)
            self.assertIn("lines 6-10", out, args)
            self.assertIn("return -x", out, args)

    def test_nested_constant_attribute_property(self):
        both = self.repo.nav("show", "pkg/nested.py", "compat_func")
        self.assertIn("return x", both)
        self.assertIn("return -x", both)
        self.assertIn("return 1", self.repo.nav("show", "pkg/nested.py", "tried"))
        self.assertIn("MAX_SIZE = 100", self.repo.nav("show", "pkg/nested.py", "MAX_SIZE"))
        self.assertIn("LIMIT = 5", self.repo.nav("show", "pkg/nested.py", "Box.LIMIT"))
        prop = self.repo.nav("show", "pkg/nested.py", "Box.plain")
        self.assertIn("@property", prop)
        self.assertIn("@plain.setter", prop)
        self.assertIn("def tried", self.repo.nav("show", "pkg/nested.py"))

    def test_symbol_defined_elsewhere(self):
        self.repo.write("pkg/user.py", "from pkg.core import build\n\n\ndef go():\n    return build(1, 2, 3)\n")
        self.repo.git("add", "pkg/user.py")
        out = self.repo.nav("show", "pkg/user.py", "build")
        self.assertIn("is not defined in pkg/user.py", out)
        self.assertIn("imported there at line 1", out)
        self.assertIn("Defined at: pkg/core.py:", out)
        self.assertIn("Next: python3 /tmp/nav.py show pkg/core.py build", out)

    def test_dotted_names_search_last_part(self):
        self.assertIn("App.run", self.repo.nav("refs", "App.build"))
        self.assertTrue(self.repo.nav("find", "App.build").startswith("pkg/core.py"))


class TestDenseOutput(unittest.TestCase):
    """More code per call: several symbols per show, code in find, long functions summarised."""

    def setUp(self):
        self.repo = Repo({"pkg/__init__.py": "", "pkg/core.py": CORE, "pkg/long.py": LONG})

    def tearDown(self):
        self.repo.close()

    def test_show_several_symbols(self):
        out = self.repo.nav("show", "pkg/core.py", "check", "build", "App.run")
        for text in ("pkg/core.py  check", "pkg/core.py  build", "pkg/core.py  App.run", "return title, version, debug"):
            self.assertIn(text, out)
        many = self.repo.nav("show", "pkg/core.py", "check", "build", "App.run", "quick", "App")
        self.assertIn("not shown: App", many)

    def test_find_prints_the_best_short_function(self):
        out = self.repo.nav("find", "build", "version")
        self.assertIn("Best match, pkg/core.py  App.run", out)
        self.assertIn("return build(title=title, version=version, debug=False)", out)

    def test_long_function_summarised_around_find_terms(self):
        self.repo.nav("find", "value_150")
        out = self.repo.nav("show", "pkg/long.py", "long_function")
        self.assertIn("(long: header and the lines with value_150", out)
        self.assertIn("value_150 = x + 150", out)
        self.assertNotIn("value_010", out)
        self.assertIn("python3 /tmp/nav.py show pkg/long.py long_function --full", out)
        full = self.repo.nav("show", "pkg/long.py", "long_function", "--full")
        self.assertIn("value_010", full)
        self.assertIn("Next page", full)

    def test_long_function_without_matching_terms_is_paged(self):
        out = self.repo.nav("show", "pkg/long.py", "long_function")
        self.assertNotIn("(long:", out)
        self.assertIn("Next page", out)


class TestStatus(unittest.TestCase):
    """The status line: calls, time, edits and code seen survive context compaction because they live in /tmp."""

    def setUp(self):
        self.repo = Repo({"pkg/__init__.py": "", "pkg/core.py": CORE})

    def tearDown(self):
        self.repo.close()

    def test_status_line_counts_calls_and_edits(self):
        first = self.repo.nav("find", "build", raw=True).splitlines()[0]
        self.assertTrue(first.startswith("[code-nav call 1 | about 6m"), first)
        self.assertIn("left of 8m", first)
        self.assertIn("edited: nothing yet", first)
        self.repo.write("pkg/core.py", CORE.replace("return value", "return value + 0"))
        second = self.repo.nav("refs", "build", raw=True).splitlines()[0]
        self.assertIn("[code-nav call 2", second)
        self.assertIn("edited: pkg/core.py +1 -1", second)
        self.assertIn("seen: core.py:App.run", second)

    def test_repeated_show_is_marked(self):
        self.repo.nav("show", "pkg/core.py", "check")
        again = self.repo.nav("show", "pkg/core.py", "check")
        self.assertIn("already shown in code-nav call 1, unchanged since", again)
        self.assertIn("value must be non-negative", again)  # the code is still printed
        self.repo.write("pkg/core.py", CORE.replace("return value", "return value + 0"))
        changed = self.repo.nav("show", "pkg/core.py", "check")
        self.assertIn("shown in code-nav call 2, changed since", changed)

    def test_reading_pauses_until_a_source_edit(self):
        for _ in range(7):  # LOCK_READS successful reads run
            self.assertNotIn("READING PAUSED", self.repo.nav("refs", "build", raw=True))
        self.assertIn("This read did not run", self.repo.nav("refs", "build", raw=True))
        self.repo.write("tests/test_x.py", "def test_x():\n    pass\n")  # a test file is not a source edit
        self.assertIn("This read did not run", self.repo.nav("refs", "build", raw=True))
        self.repo.write("pkg/core.py", CORE.replace("return value", "return value + 0"))
        self.assertNotIn("READING PAUSED", self.repo.nav("refs", "build", raw=True))


if __name__ == "__main__":
    unittest.main()
