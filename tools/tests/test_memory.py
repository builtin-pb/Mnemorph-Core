"""Exercise maintenance operations against actual temporary Git histories."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("memory_tool", Path(__file__).parents[1] / "memory.py")
memory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(memory)


class MemoryToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.git("init", "-q")
        self.git("config", "user.name", "Memory tests")
        self.git("config", "user.email", "memory@example.invalid")
        self.config({"default": 8000, "indexed_src": False, "include": ["**"], "rules": [], "files": {}})
        self.write("seed.md", "seed\n")
        self.commit("Initial")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.PIPE).decode().strip()

    def write(self, name, text):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def config(self, value):
        self.write("memory-limits.json", json.dumps(value))

    def commit(self, subject):
        self.git("add", "-A")
        self.git("commit", "-qm", subject)
        return self.git("rev-parse", "HEAD")

    def run_cli(self, *args, error=False, over=False):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = memory.main(["--root", str(self.root), *args])
            except SystemExit as exc:
                code = exc.code
        if error:
            self.assertEqual(code, 2, (stdout.getvalue(), stderr.getvalue()))
            self.assertTrue(stderr.getvalue())
            return stderr.getvalue()
        self.assertEqual(code, 1 if over else 0, stderr.getvalue())
        return json.loads(stdout.getvalue())

    def test_deleted_renamed_file_history_and_exact_recovery(self):
        self.write("old.md", "needle [a.*] literal\n")
        created = self.commit("Create")
        self.git("mv", "old.md", "renamed.md")
        renamed = self.commit("Rename")
        self.git("rm", "renamed.md")
        deleted = self.commit("Delete")
        rows = self.run_cli("history", "--path", "renamed.md")["records"]
        self.assertEqual([r["commit"] for r in rows], [deleted, renamed, created])
        self.assertEqual(rows[0]["parents"], [renamed])
        self.assertIn("old.md", rows[1]["paths"])
        matched = self.run_cli("history", "--path", "renamed.md", "--query", "[a.*]")["records"]
        self.assertEqual([r["commit"] for r in matched], [deleted, created])
        recovered = self.run_cli("show", created, "old.md")
        self.assertEqual(recovered["text"], "needle [a.*] literal\n")
        self.assertEqual(recovered["blob"], self.git("rev-parse", created + ":old.md"))
        self.assertFalse((self.root / "old.md").exists())
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_query_is_literal_and_history_is_bounded(self):
        for index in range(3):
            self.write("target.md", "[a.*]" * (index + 1))
            self.commit(f"Edit {index}")
        result = self.run_cli("history", "--query", "[a.*]", "--limit", "2")
        self.assertEqual(len(result["records"]), 2)
        self.assertTrue(result["truncated"])
        self.assertEqual(self.run_cli("history", "--query", "[a.+]")["records"], [])
        self.run_cli("history", error=True)

    def test_directory_history_and_literal_pathspec(self):
        self.write("a[x]/file.md", "one")
        revision = self.commit("Directory")
        self.assertEqual(self.run_cli("history", "--path", "a[x]")["records"][0]["commit"], revision)
        self.assertEqual(self.run_cli("history", "--path", "ax")["records"], [])

    def test_exact_match_path_survives_large_commit(self):
        for index in range(40):
            self.write(f"a-{index:02}.md", "unrelated")
        self.write("z-matching.md", "rare exact phrase")
        self.commit("Broad change")
        queried = self.run_cli("history", "--query", "rare exact phrase")["records"][0]
        self.assertEqual(queried["paths"], ["z-matching.md"])
        self.assertFalse(queried["paths_truncated"])
        scoped = self.run_cli("history", "--path", "z-matching.md")["records"][0]
        self.assertEqual(scoped["paths"], ["z-matching.md"])
        self.assertFalse(scoped["paths_truncated"])
        whole = self.run_cli("history", "--path", ".", "--limit", "1")["records"][0]
        self.assertEqual(len(whole["paths"]), 30)
        self.assertTrue(whole["paths_truncated"])

    def test_rename_paths_stay_visible_across_broad_commits(self):
        for index in range(40):
            self.write(f"a-{index:02}.md", "unrelated")
        self.write("z-old.md", "distinct content")
        self.commit("Broad creation")
        for index in range(40):
            self.write(f"a-{index:02}.md", "unrelated change")
        self.git("mv", "z-old.md", "z-new.md")
        self.commit("Broad rename")
        rows = self.run_cli("history", "--path", "z-new.md")["records"]
        self.assertEqual(rows[0]["paths"][:2], ["z-old.md", "z-new.md"])
        self.assertEqual(rows[1]["paths"][0], "z-old.md")

    def test_path_query_retains_old_name_when_rename_does_not_change_count(self):
        for index in range(40):
            self.write(f"a-{index:02}.md", "shared exact phrase")
        self.write("z-old.md", "shared exact phrase\ndistinct file content\n")
        created = self.commit("Many matching files")
        self.git("mv", "z-old.md", "z-new.md")
        self.commit("Unchanged contents at new name")
        self.write("z-new.md", "shared exact phrase\ndistinct file content\nshared exact phrase\n")
        edited = self.commit("Add another occurrence")
        rows = self.run_cli("history", "--path", "z-new.md", "--query", "shared exact phrase")["records"]
        self.assertEqual([row["commit"] for row in rows], [edited, created])
        self.assertEqual(rows[0]["paths"], ["z-new.md"])
        self.assertEqual(rows[1]["paths"], ["z-old.md"])
        self.assertFalse(rows[1]["paths_truncated"])
        self.assertIn("distinct file content", self.run_cli("show", created, rows[1]["paths"][0])["text"])

    def test_deleted_directory_history(self):
        self.write("gone/a.md", "first")
        created = self.commit("Create directory")
        self.write("gone/b.md", "second")
        expanded = self.commit("Expand directory")
        self.git("rm", "-r", "gone")
        deleted = self.commit("Delete directory")
        rows = self.run_cli("history", "--path", "gone")["records"]
        self.assertEqual([r["commit"] for r in rows], [deleted, expanded, created])

    def test_historical_file_kind_does_not_follow_the_current_working_tree(self):
        self.write("old.md", "a historical account")
        created = self.commit("Create old account")
        self.git("mv", "old.md", "subject")
        moved = self.commit("Rename the account")
        self.git("rm", "subject")
        self.write("subject/new.md", "now a directory")
        self.commit("Replace file with directory")
        rows = self.run_cli("history", "--path", "subject", "--revision", moved)["records"]
        self.assertEqual([row["commit"] for row in rows], [moved, created])
        self.assertEqual(rows[1]["paths"], ["old.md"])
        self.assertEqual(self.run_cli("show", created, rows[1]["paths"][0])["text"], "a historical account")

    def test_query_finds_paths_when_text_arrives_by_directory_rename(self):
        self.write("old/account.md", "rare exact observation")
        self.commit("Old account")
        self.git("mv", "old", "new")
        moved = self.commit("Move subject")
        rows = self.run_cli("history", "--path", "new", "--query", "rare exact observation")["records"]
        self.assertEqual(rows[0]["commit"], moved)
        self.assertEqual(rows[0]["paths"], ["new/account.md", "old/account.md"])

    def test_directory_query_excludes_unrelated_matches_in_the_same_commit(self):
        self.write("old/account.md", "shared phrase\ndistinct account")
        self.write("unrelated.md", "shared phrase")
        self.commit("Both accounts")
        self.git("mv", "old", "new")
        self.write("unrelated.md", "shared phrase\nshared phrase")
        moved = self.commit("Move one account and edit another")
        row = self.run_cli("history", "--path", "new", "--query", "shared phrase")["records"][0]
        self.assertEqual(row["commit"], moved)
        self.assertEqual(row["paths"], ["new/account.md", "old/account.md"])

    def test_history_preserves_newlines_and_marker_names_in_paths(self):
        names = ["COMMIT", "name\nCOMMIT\nnote.md"]
        for name in names:
            self.write(name, "unusual-name observation")
        created = self.commit("Literal paths")
        rows = self.run_cli("history", "--query", "unusual-name observation")["records"]
        self.assertEqual(rows[0]["commit"], created)
        self.assertEqual(set(rows[0]["paths"]), set(names))
        for name in names:
            scoped = self.run_cli("history", "--path", name)["records"][0]
            self.assertEqual(scoped["paths"], [name])
            self.assertEqual(self.run_cli("show", scoped["commit"], name)["text"], "unusual-name observation")

    def test_merge_resolution_is_recoverable_through_a_later_rename(self):
        self.write("subject/account.md", "base account\n")
        created = self.commit("Create account")
        main_branch = self.git("branch", "--show-current")
        self.git("checkout", "-qb", "other")
        self.write("subject/account.md", "other account\n")
        other = self.commit("Other interpretation")
        self.git("checkout", "-q", main_branch)
        self.write("subject/account.md", "main account\n")
        main = self.commit("Main interpretation")
        with self.assertRaises(subprocess.CalledProcessError):
            self.git("merge", "other", "--no-edit")
        self.write("subject/account.md", "resolved combined account\n")
        merged = self.commit("Reconcile interpretations")
        self.git("mv", "subject/account.md", "subject/renamed.md")
        renamed = self.commit("Rename resolved account")
        for scope in [None, "subject", "subject/renamed.md"]:
            with self.subTest(scope=scope):
                path_args = ["--path", scope] if scope else []
                result = self.run_cli("history", *path_args, "--query", "combined", "--limit", "1")
                self.assertFalse(result["truncated"])
                self.assertEqual([row["commit"] for row in result["records"]], [merged])
                row = result["records"][0]
                self.assertEqual(row["parents"], [main, other])
                self.assertEqual(row["paths"], ["subject/account.md"])
                self.assertEqual(self.run_cli("show", merged, row["paths"][0])["text"], "resolved combined account\n")
                if scope:
                    rows = self.run_cli("history", *path_args)["records"]
                    self.assertEqual(len(rows), 5)
                    self.assertEqual({row["commit"] for row in rows}, {created, main, other, merged, renamed})

    def test_commit_rationale_is_separate_and_bounded(self):
        parent = self.git("rev-parse", "HEAD")
        self.write("seed.md", "reconstructed")
        message = "Reconstruct account\n\nWhy: repeated examples obscure the rule.\n\nAccepted loss: exact wording requires historical recovery."
        commit = self.commit(message)
        result = self.run_cli("show", commit)
        self.assertEqual(result["parents"], [parent])
        self.assertEqual(result["commit"], commit)
        self.assertEqual(result["text"], message + "\n")
        clipped = self.run_cli("show", commit, "--max-chars", "30")
        self.assertEqual(clipped["text"], message[:30])
        self.assertTrue(clipped["chars_truncated"])
        self.assertNotIn("blob", clipped)

    def test_line_and_unicode_character_clipping(self):
        self.write("unicode.md", "before\n日本🙂abcd\nafter\n")
        revision = self.commit("Unicode")
        result = self.run_cli("show", revision, "unicode.md", "--start-line", "2", "--lines", "1", "--max-chars", "4")
        self.assertEqual(result["text"], "日本🙂a")
        self.assertTrue(result["chars_truncated"])
        self.assertTrue(result["lines_truncated"])
        self.assertEqual(result["total_chars"], len("before\n日本🙂abcd\nafter\n"))
        self.assertEqual(self.run_cli("show", revision, "unicode.md", "--start-line", "99")["text"], "")

    def test_show_pages_long_lines_without_losing_characters(self):
        body = "before\n" + "日本🙂\\\"\t" * 4000 + "\nafter\nlast"
        self.write("long.json", body)
        commit = self.commit("Long source record")
        first = self.run_cli("show", commit, "long.json", "--start-line", "2", "--lines", "1")
        self.assertEqual(first["start_offset"], len("before\n"))
        self.assertEqual(first["text"], body[len("before\n"):len("before\n") + 6000])
        pages, cursor = [first["text"]], first["next_offset"]
        self.write("long.json", "Different current contents\n")
        self.commit("Move HEAD during recovery")
        while cursor is not None:
            result = self.run_cli("show", first["commit"], "long.json", "--offset", str(cursor), "--lines", "1", "--max-chars", "997")
            self.assertEqual(result["blob"], first["blob"])
            self.assertEqual(result["start_offset"], cursor)
            self.assertLessEqual(len(result["text"]), 997)
            pages.append(result["text"])
            if result["next_offset"] is not None:
                self.assertGreater(result["next_offset"], cursor)
            cursor = result["next_offset"]
        self.assertEqual("".join(pages), body[len("before\n"):])

    def test_show_offsets_cover_line_and_end_boundaries(self):
        body = "a\r\nb\v\u2028c\n\nlast"
        self.write("boundaries.txt", body)
        commit = self.commit("Physical lines")
        for offset in range(len(body) + 1):
            with self.subTest(offset=offset):
                result = self.run_cli("show", commit, "boundaries.txt", "--offset", str(offset), "--lines", "1", "--max-chars", "2")
                stop = body.find("\n", offset)
                selected = body[offset:stop + 1 if stop >= 0 else len(body)]
                self.assertEqual(result["text"], selected[:2])
                self.assertEqual(result["total_lines"], 4)
                self.assertEqual(result["start_line"], body[:offset].count("\n") + 1)
                next_offset = offset + len(result["text"])
                self.assertEqual(result["next_offset"], next_offset if next_offset < len(body) else None)
        self.write("empty.txt", "")
        empty_commit = self.commit("Empty")
        empty = self.run_cli("show", empty_commit, "empty.txt", "--offset", "0")
        self.assertEqual((empty["text"], empty["total_lines"], empty["next_offset"]), ("", 0, None))

    def test_show_commit_message_can_resume_after_clipping(self):
        message = "Reconstruction\n\n" + "Reason 日本🙂\n" * 30
        self.write("seed.md", "Changed\n")
        commit = self.commit(message)
        expected = subprocess.check_output(["git", "-C", str(self.root), "cat-file", "commit", commit]).split(b"\n\n", 1)[1].decode()
        parts, offset = [], 0
        while offset is not None:
            result = self.run_cli("show", commit, "--offset", str(offset), "--max-chars", "19")
            parts.append(result["text"])
            offset = result["next_offset"]
        self.assertEqual("".join(parts), expected)

    def test_show_rejects_ambiguous_or_invalid_offsets(self):
        self.run_cli("show", "HEAD", "seed.md", "--offset", "0", "--start-line", "1", error=True)
        self.run_cli("show", "HEAD", "seed.md", "--offset", "-1", error=True)
        self.assertIn("offset", self.run_cli("show", "HEAD", "seed.md", "--offset", "6", error=True))
        self.assertIn("offset", self.run_cli("show", "HEAD", "--offset", "100000", error=True))

    def test_sizes_precedence_newfiles_and_exact_unicode_counts(self):
        self.config({"default": 10, "indexed_src": False, "include": ["**"], "rules": [{"pattern": "**/*.md", "limit": 8},
                     {"pattern": "area/**", "limit": 6}], "files": {"area/exact.md": 3}})
        self.write("area/exact.md", "日本🙂é")
        self.write("area/nested/a.md", "1234567")
        self.write("new.txt", "a" * 11)
        rows = {r["path"]: r for r in self.run_cli("sizes", "--all", over=True)["records"]}
        self.assertEqual(rows["area/exact.md"]["chars"], 4)
        self.assertEqual(rows["area/exact.md"]["limit"], 3)
        self.assertEqual(rows["area/nested/a.md"]["limit"], 6)
        self.assertEqual(rows["seed.md"]["limit"], 8)
        self.assertEqual(rows["new.txt"]["limit"], 10)
        selected = self.run_cli("sizes", "--path", "area", "--limit", "1", over=True)
        self.assertEqual(selected["text_files"], 2)
        self.assertEqual(selected["total_chars"], 11)
        self.assertEqual(selected["records"][0]["path"], "area/exact.md")
        self.assertTrue(selected["truncated"])

    def test_ignored_binary_and_symlink_are_not_text(self):
        self.write(".gitignore", "ignored.txt\n")
        self.write("ignored.txt", "invisible")
        (self.root / "binary.dat").write_bytes(b"\xff\x00")
        (self.root / "link").symlink_to("seed.md")
        result = self.run_cli("sizes", "--all")
        self.assertEqual(result["skipped"]["binary"], 1)
        self.assertEqual(result["skipped"]["symlink"], 1)
        self.assertNotIn("ignored.txt", [r["path"] for r in result["records"]])
        self.assertEqual(result["text_files"], 3)

    def test_size_scope_is_hard_and_overrides_are_exact(self):
        self.config({"default": 6, "indexed_src": False, "include": ["src/**/*.md", "integrations/codex/skills/**/*.md", "AGENTS.md"],
                     "rules": [], "files": {"src/large.md": 12}})
        self.write("src/large.md", "123456789012")
        self.write("src/small.md", "1234567")
        self.write("src/raw.json", "x" * 100)
        self.write("integrations/codex/skills/taste/SKILL.md", "short")
        self.write("AGENTS.md", "short")
        result = self.run_cli("sizes", "--all", over=True)
        self.assertEqual(result["over_limit_files"], 1)
        self.assertEqual(result["text_files"], 4)
        self.assertGreaterEqual(result["skipped"]["out_of_scope"], 2)
        self.assertEqual([r["path"] for r in result["records"] if r["over_by"]],
                         ["src/small.md"])
        self.write("src/small.md", "123456")
        self.assertEqual(self.run_cli("sizes")["over_limit_files"], 0)

    def test_indexed_subject_scope_omits_raw_markdown_evidence(self):
        self.config({"default": 80, "indexed_src": True, "include": ["AGENTS.md"],
                     "rules": [], "files": {}})
        self.write("src/subject.md", "---\nid: sample\nsummary: A subject.\n---\n\n# Subject\n")
        (self.root / "src/crlf.md").write_bytes(b"---\r\nid: crlf\r\nsummary: A subject.\r\n---\r\n# Subject\r\n")
        self.write("src/raw.md", "x" * 200)
        self.write("AGENTS.md", "instructions")
        result = self.run_cli("sizes", "--all")
        self.assertEqual(result["text_files"], 3)
        self.assertEqual({r["path"] for r in result["records"]},
                         {"AGENTS.md", "src/subject.md", "src/crlf.md"})

    def test_missing_or_ungoverned_exact_override_fails_whole_check(self):
        self.config({"default": 80, "indexed_src": True, "include": ["AGENTS.md"],
                     "rules": [], "files": {"src/raw.md": 1000}})
        self.write("src/raw.md", "Raw evidence")
        self.assertIn("no governed file", self.run_cli("sizes", error=True))

    def test_instance_limits_extend_the_shared_policy(self):
        self.config({"default": 6, "indexed_src": False, "include": ["src/**/*.md"],
                     "rules": [{"pattern": "src/**/README.md", "limit": 4}], "files": {"src/core/long.md": 9}})
        self.write("src/core/long.md", "123456789")
        self.write("src/personal/README.md", "12345")
        self.write("src/personal/life.md", "1234567890")
        self.assertEqual(self.run_cli("sizes", over=True)["over_limit_files"], 2)
        self.write("src/memory-limits.json", json.dumps({"rules": [{"pattern": "src/personal/**", "limit": 5}],
                                                         "files": {"src/personal/life.md": 10}}))
        rows = {r["path"]: r for r in self.run_cli("sizes", "--all")["records"]}
        self.assertEqual((rows["src/personal/README.md"]["limit"], rows["src/personal/life.md"]["limit"]), (5, 10))
        self.assertEqual(rows["src/core/long.md"]["limit"], 9)
        for value in ({"default": 3}, {"files": {"docs/x.md": 3}}, {"files": {"src/gone.md": 3}},
                      {"rules": [{"pattern": "src/**", "limit": 0}]}, [1]):
            with self.subTest(value=value):
                self.write("src/memory-limits.json", json.dumps(value))
                self.run_cli("sizes", error=True)

    def test_missing_config_fails_and_unstaged_deletion_is_not_sized(self):
        (self.root / "memory-limits.json").unlink()
        self.assertIn("memory-limits.json", self.run_cli("sizes", error=True))
        self.config({"default": 8000, "indexed_src": False, "include": ["**"], "rules": [], "files": {}})
        (self.root / "seed.md").unlink()
        result = self.run_cli("sizes", "--all")
        self.assertEqual(result["skipped"]["deleted"], 1)
        self.assertNotIn("seed.md", [row["path"] for row in result["records"]])

    def test_invalid_config_fails(self):
        values = [None, {}, {"default": True, "indexed_src": False, "include": ["**"], "rules": [], "files": {}},
                  {"default": 10, "indexed_src": False, "include": [], "rules": [], "files": {}},
                  {"default": 10, "indexed_src": False, "include": ["../outside"], "rules": [], "files": {}},
                  {"default": 10, "indexed_src": False, "include": ["**"], "rules": [{"pattern": "**", "limit": 0}], "files": {}},
                  {"default": 10, "indexed_src": False, "include": ["**"], "rules": [], "files": {"../outside": 3}},
                  {"default": 10, "indexed_src": False, "include": ["src/**/*.md"], "rules": [], "files": {"docs/x.md": 3}}]
        for value in values:
            with self.subTest(value=value):
                self.config(value)
                self.run_cli("sizes", error=True)
        self.write("memory-limits.json", "{broken")
        self.run_cli("sizes", error=True)

    def test_path_escapes_option_injection_and_caps_rejected(self):
        for path in ["../outside", "/tmp/outside", "a/../../outside", ".git/config", ":(glob)*", "--all"]:
            with self.subTest(path=path):
                self.run_cli("history", "--path=" + path, error=True)
        self.run_cli("history", "--path", "seed.md", "--revision=--all", error=True)
        self.run_cli("history", "--path", "seed.md", "--limit", "51", error=True)
        self.run_cli("show", "HEAD", "seed.md", "--lines", "201", error=True)
        self.run_cli("show", "HEAD", "seed.md", "--max-chars", "20001", error=True)
        self.run_cli("sizes", "--limit", "101", error=True)

    def test_show_binary_tree_or_unknown_revision_fails(self):
        (self.root / "binary.dat").write_bytes(b"\x00")
        self.write("dir/file.md", "body")
        self.commit("Files")
        self.run_cli("show", "HEAD", "binary.dat", error=True)
        self.run_cli("show", "HEAD", "dir", error=True)
        self.run_cli("show", "not-a-revision", "seed.md", error=True)

    def test_glob_directory_semantics(self):
        self.assertTrue(memory.glob_matches("__entry__.md", "**/__entry__.md"))
        self.assertTrue(memory.glob_matches("a/b/__entry__.md", "**/__entry__.md"))
        self.assertFalse(memory.glob_matches("a/b/file.md", "a/*.md"))
        self.assertTrue(memory.glob_matches("a/b/file.md", "a/**/*.md"))


if __name__ == "__main__":
    unittest.main()
