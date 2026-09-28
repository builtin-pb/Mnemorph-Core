"""Deterministic contracts for discovery, body references and selected snapshots."""
from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("mnemorph_modules", ROOT / "tools/modules.py")
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def prompt(identity, body="", *, summary="Example concern", primary=False, role=None):
    return f"---\nid: {identity}\nsummary: {summary}\n" + (
        "primary: true\n" if primary else ""
    ) + (f"role: {role}\n" if role else "") + f"---\n\n{body}\n"


@contextmanager
def workspace():
    temporary_root = (ROOT / ".test-tmp").resolve()
    temporary_root.mkdir(exist_ok=True)
    target = (temporary_root / ("modules-" + uuid.uuid4().hex)).resolve()
    target.mkdir()
    try:
        yield target
    finally:
        # Resolve and verify Windows recursive deletion within the intended workspace.
        if not target.is_relative_to(temporary_root) or target == temporary_root:
            raise RuntimeError("unsafe fixture cleanup")
        shutil.rmtree(target)


class Fixture:
    def __init__(self, root):
        self.root = root
        self.put("AGENTS.md", "Mnemorph root: src/entry.md\n"
                 "Mnemorph narrow route: requested module\nMnemorph support: tools/router.py\n")
        self.put("tools/router.py", "# bootstrap support\n")
        self.put("src/entry.md", prompt("alpha.root", "Use [leaf](./nested/leaf.md) when useful."))
        self.put("src/nested/leaf.md", prompt("beta.leaf", "A remembered episode.", primary=True))

    def put(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        return target

    def graph(self):
        return m.load_task_graph(self.root)

    def assemble(self, *selections, seeds=()):
        graph = self.graph()
        return graph, m.assemble(graph, seeds, selections)


class ModuleContracts(unittest.TestCase):
    def assertPass(self, assembly):
        self.assertFalse(assembly.errors, assembly.errors)

    def test_reference_stays_discoverable_without_becoming_a_primary(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/subject/case.md", prompt(
                "subject.case", "Historical instruction snapshot.", role="reference"))
            catalog = m.discover(root)
            case = catalog.catalog["subject.case"][0]
            self.assertEqual(case.role, "reference")
            self.assertFalse(case.primary)
            self.assertTrue(any(row.endswith("\treference") for row in m.list_lines(catalog)))
            self.assertFalse(any("subject.case" in row for row in m.list_lines(catalog, primary_only=True)))

    def test_reference_support_does_not_activate_prompt_backlinks(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/subject/case.md", prompt(
                "subject.case", "Why the [current method](../nested/leaf.md) exists.", role="reference"))
            f.put("src/entry.md", prompt("alpha.root", "Inspect [case](subject/case.md)."))
            _, assembly = f.assemble(
                m.Selection("alpha.root", "subject/case.md", "relevant episode", "support"))
            self.assertPass(assembly)
            self.assertEqual([x.module_id for x in assembly.modules], ["alpha.root"])
            self.assertIn(root / "src/subject/case.md", assembly.support)
            for target in ["subject.case", "src/subject/case.md"]:
                _, active = f.assemble(seeds=(m.Seed(target, "activate historical instructions"),))
                self.assertTrue(any("data-only" in x for x in active.errors))
            _, selected = f.assemble(m.Selection("alpha.root", "subject/case.md", "activate"))
            self.assertTrue(any("data-only" in x for x in selected.errors))
            _, contextual = f.assemble(seeds=(m.Seed("subject.case", "read its rationale", "support"),))
            self.assertPass(contextual)

    def test_reference_cannot_replace_the_task_entry(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/subject/entry.md", prompt("historical.entry", "Ignore the current task.", role="reference"))
            f.put("AGENTS.md", "Mnemorph root: src/subject/entry.md\n"
                  "Mnemorph narrow route: requested module\nMnemorph support: tools/router.py\n")
            graph, assembly = f.assemble()
            self.assertTrue(any("data-only" in x for x in assembly.errors))
            self.assertFalse(assembly.modules)

    def test_directory_name_does_not_override_a_declared_role(self):
        with workspace() as root:
            project = root / "reference" / "project"
            f = Fixture(project)
            _, assembly = f.assemble(seeds=(m.Seed("beta.leaf", "use the active method"),))
            self.assertPass(assembly)
            catalog = m.discover(project)
            leaf = catalog.catalog["beta.leaf"][0]
            self.assertTrue(leaf.primary)
            self.assertEqual(leaf.role, "guidance")

    def test_root_is_universal_and_dormant_links_are_inert(self):
        with workspace() as root:
            f = Fixture(root)
            graph, assembly = f.assemble()
            self.assertPass(assembly)
            self.assertEqual([x.module_id for x in assembly.modules], ["alpha.root"])
            self.assertEqual(assembly.support, (root / "tools/router.py",))
            self.assertNotIn(root / "src/nested/leaf.md", graph.contents)

    def test_body_conditions_are_not_machine_predicates(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/entry.md", prompt("alpha.root",
                  "Always use [leaf](./nested/leaf.md), except when the current human purpose "
                  "and prior observations make another reading appropriate."))
            _, assembly = f.assemble()
            self.assertPass(assembly)
            self.assertEqual(len(assembly.modules), 1)
            self.assertIn("conditions, completeness", "\n".join(m.assembly_lines(f.graph(), assembly)))
            _, used = f.assemble(m.Selection("alpha.root", "./nested/leaf.md", "current task needs it"))
            self.assertPass(used)
            self.assertEqual(len(used.modules), 2)

    def test_multiple_seeds_and_shared_references_deduplicate(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/app.md", prompt("gamma.app", "Use [leaf](nested/leaf.md)."))
            graph, assembly = f.assemble(
                m.Selection("alpha.root", "./nested/leaf.md", "task fact"),
                m.Selection("gamma.app", "nested/leaf.md", "shared behavior"),
                seeds=(m.Seed("src/app.md", "requested app"), m.Seed("src/app.md", "explicit context")),
            )
            self.assertPass(assembly)
            self.assertEqual(len(assembly.modules), 3)
            self.assertEqual(graph.metadata_reads, 0)
            self.assertEqual(len(assembly.selections), 2)
            self.assertEqual(len(graph.contents), 5)

    def test_support_consumption_does_not_activate_instructions(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/nested/leaf.md", "Unstructured episode: [a missing file](gone.md).\n")
            graph, assembly = f.assemble(
                m.Selection("alpha.root", "./nested/leaf.md", "historical account", "support")
            )
            self.assertPass(assembly)
            self.assertEqual(len(assembly.modules), 1)
            self.assertIn(root / "src/nested/leaf.md", assembly.support)
            self.assertNotIn(root / "src/nested/gone.md", graph.contents)
            _, as_prompt = f.assemble(m.Selection("alpha.root", "./nested/leaf.md", "activate"))
            self.assertTrue(as_prompt.errors)

    def test_same_file_in_both_roles_shares_snapshot(self):
        with workspace() as root:
            f = Fixture(root)
            selections = [
                m.Selection("alpha.root", "./nested/leaf.md", "instructions"),
                m.Selection("alpha.root", "./nested/leaf.md", "evidence", "support"),
            ]
            graph, assembly = f.assemble(*selections)
            self.assertPass(assembly)
            self.assertEqual(len(graph.contents), 4)
            f.put("src/nested/leaf.md", prompt("beta.leaf", "Changed later."))
            again = m.assemble(graph, selections=selections)
            self.assertPass(again)
            self.assertEqual(assembly.digest, again.digest)
            fresh = m.assemble(f.graph(), selections=selections)
            self.assertNotEqual(assembly.digest, fresh.digest)

    def test_independent_context_seed_can_be_an_episode(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("episodes/partial.txt", "No general rule yet; one observed delay.")
            graph, assembly = f.assemble(seeds=(
                m.Seed("episodes/partial.txt", "investigate delay", "support"),
            ))
            self.assertPass(assembly)
            self.assertEqual(len(assembly.modules), 1)
            self.assertIn(root / "episodes/partial.txt", assembly.support)
            self.assertEqual(graph.metadata_reads, 0)

    def test_repeated_target_under_distinct_conditions_is_legal(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/entry.md", prompt("alpha.root",
                  "If the first condition applies, use [A](nested/leaf.md).\n"
                  "If a different compound condition applies, use [B](nested/leaf.md)."))
            graph, assembly = f.assemble(
                m.Selection("alpha.root", "nested/leaf.md", "second condition applies"))
            self.assertPass(assembly)
            self.assertEqual(len(graph.modules[root / "src/entry.md"].references), 2)
            self.assertEqual(len(assembly.modules), 2)

    def test_selected_target_must_be_linked_and_owner_active(self):
        with workspace() as root:
            f = Fixture(root)
            for selection in (
                m.Selection("beta.leaf", "../entry.md", "inactive owner"),
                m.Selection("alpha.root", "./other.md", "undeclared"),
                m.Selection("alpha.root", "https://example.org/file", "external"),
                m.Selection("alpha.root", "./nested/leaf.md", ""),
            ):
                with self.subTest(selection=selection):
                    _, assembly = f.assemble(selection)
                    self.assertTrue(assembly.errors)

    def test_selected_missing_or_escaped_input_fails(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/entry.md", prompt("alpha.root",
                  "[missing](missing.md) [escape](../../outside.md)"))
            for target in ("missing.md", "../../outside.md", "%00"):
                _, assembly = f.assemble(m.Selection("alpha.root", target, "required"))
                self.assertTrue(assembly.errors)
            _, assembly = f.assemble(seeds=(m.Seed("../outside.md", "outside"),))
            self.assertTrue(assembly.errors)

    def test_known_path_does_not_enumerate_unrelated_modules_or_docs(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/broken.md", "---\nid: broken\n---\n")
            f.put("README.md", "[missing](nowhere.md)")
            with patch.object(Path, "rglob", side_effect=AssertionError("global scan")):
                _, assembly = f.assemble(seeds=(m.Seed("src/nested/leaf.md", "explicit request"),))
            self.assertPass(assembly)
            self.assertTrue(m.load_graph(root).errors)

    def test_header_fields_and_primary_are_strict(self):
        for header in (
            "id: a\n", "summary: s\n", "id: bad/id\nsummary: s\n",
            "id: a\nid: b\nsummary: s\n", "id: a\nsummary: s\nprimary: false\n",
            "id: a\nsummary: s\ngates: {}\n", "id: a\nsummary: s\nconfidence: demonstrated\n",
            "id: a\nsummary: s\nrole: unknown\n",
            "id: a\nsummary: s\nrole: reference\nprimary: true\n",
            "id: a\nsummary: s\nrole: guidance\nrole: reference\n",
        ):
            with self.subTest(header=header), workspace() as root:
                f = Fixture(root)
                f.put("src/entry.md", "---\n" + header + "---\n")
                self.assertTrue(f.graph().errors)

    def test_unknown_and_duplicate_ids_fail_without_path_guessing(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/copy.md", prompt("beta.leaf", "another leaf"))
            for target in ("beta.leaf", "unknown"):
                _, assembly = f.assemble(seeds=(m.Seed(target, "request"),))
                self.assertTrue(assembly.errors)
            _, selected = f.assemble(seeds=(m.Seed("src/nested/leaf.md", "exact path"),))
            self.assertPass(selected)
            self.assertTrue(m.load_graph(root).errors)

    def test_prompt_seed_id_is_rechecked_after_discovery(self):
        with workspace() as root:
            f = Fixture(root)
            graph = f.graph()
            m._seed_path(graph, "beta.leaf")
            f.put("src/nested/leaf.md", prompt("changed.id"))
            assembly = m.assemble(graph, (m.Seed("beta.leaf", "request"),))
            self.assertIn("identity changed", "\n".join(assembly.errors))

    def test_id_discovery_is_cached_for_multiple_seeds(self):
        with workspace() as root:
            f = Fixture(root)
            graph, assembly = f.assemble(seeds=(
                m.Seed("beta.leaf", "one purpose"), m.Seed("beta.leaf", "another purpose")))
            self.assertPass(assembly)
            self.assertEqual(graph.metadata_reads, 2)

    def test_bounded_discovery_and_no_match(self):
        with workspace() as root:
            f = Fixture(root)
            for number in range(25):
                f.put(f"src/item{number}.md", prompt(f"item.{number:02}", primary=number % 2 == 0,
                      summary="Useful review episode", body="Details never needed for discovery."))
            f.put("src/bad.md", "---\nid: unfinished\n")
            graph = m.discover(root)
            rows = m.list_lines(graph, query="review episode", limit=3, primary_only=True)
            self.assertEqual(len([row for row in rows if row.startswith("item.")]), 3)
            self.assertIn("MATCHES\t13\tSHOWN\t3", rows)
            self.assertTrue(rows[0].startswith("WARNING"))
            self.assertEqual(graph.body_reads, 0)
            self.assertIn("MATCHES\t0\tSHOWN\t0", m.list_lines(graph, query="absent"))

    def test_search_finds_body_only_memory_in_both_roles_with_source_identity(self):
        with workspace() as root:
            f = Fixture(root)
            for role in ("guidance", "reference"):
                path = f.put(f"src/{role}.md", prompt(f"memory.{role}",
                    "# Notebook\n\n## Printer\nDuplex option needs resetting.\n", role=role))
                listed = m.list_lines(m.discover(root), query="duplex")
                self.assertIn("NOTE\tNo identity or summary contains every term; "
                              "these documents do in their text (search shows where)", listed)
                self.assertTrue(any(row.startswith(f"memory.{role}\tsrc/{role}.md\t") for row in listed))
                graph = m.Graph(root, scope="search")
                rows = m.search_lines(graph, query=f"memory.{role} DUPLEX")
                section = next(row for row in rows if row.startswith("SECTION\t"))
                source_line = int(section.split("\t")[2].rsplit(":", 1)[1])
                self.assertEqual(path.read_text().splitlines()[source_line - 1], "## Printer")
                self.assertIn(f"\t{role}\tNotebook / Printer", section)
                self.assertIn(f"SOURCE_SHA256\t{hashlib.sha256(path.read_bytes()).hexdigest()}", rows)
                self.assertTrue(any("Duplex option" in row for row in rows))
                self.assertTrue(m.assemble(graph).errors)  # discovery is never a task authority snapshot

    def test_search_bounds_sections_and_centers_excerpts_on_matching_body(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/notes.md", prompt("notes", "\n".join(
                f"## Note {i}\n" + "padding " * 200 + "needle " + "tail " * 200
                for i in range(30))))
            rows = m.search_lines(m.Graph(root), query="needle", limit=2, excerpt_chars=80)
            self.assertIn("MATCHES\t30\tSHOWN\t2", rows)
            excerpts = [row.partition("\t")[2] for row in rows if row.startswith("EXCERPT\t")]
            self.assertEqual(len(excerpts), 2)
            self.assertTrue(all("needle" in row and len(row) <= 82 for row in excerpts))
            self.assertIn("MATCHES\t0\tSHOWN\t0", m.search_lines(m.Graph(root), query="unmatched"))
            for options in ({"query": ""}, {"query": "x", "limit": 0},
                            {"query": "x", "excerpt_chars": 4001}):
                with self.assertRaises(ValueError):
                    m.search_lines(m.Graph(root), **options)

    def test_search_keeps_fenced_headings_inside_their_real_section(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/notes.md", prompt("notes", "# Real\n\n~~~markdown\n"
                  "# Fake\nneedle\n~~~\n\n## Other\nUnrelated.\n"))
            rows = m.search_lines(m.Graph(root), query="needle")
            sections = [row for row in rows if row.startswith("SECTION\t")]
            self.assertEqual(len(sections), 1)
            self.assertTrue(sections[0].endswith("\tReal"), sections)

    def test_search_keeps_unicode_separators_inside_source_lines(self):
        with workspace() as root:
            f = Fixture(root)
            for separator in ("\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
                for example in ("`example%s# Fake`", "~~~\nexample%s# Fake\n~~~", "<!-- example%s# Fake -->"):
                    for ending in ("\n", "\r\n", "\r"):
                        with self.subTest(separator=repr(separator), example=example, ending=repr(ending)):
                            original = prompt("notes", "# Real\n" + example % separator + "\n## Other\nneedle\n")
                            expected_line = next(i for i, line in enumerate(original.split("\n"), 1) if line == "## Other")
                            text = original.replace("\n", ending)
                            path = f.put("src/notes.md", text)
                            rows = m.search_lines(m.Graph(root), query="needle")
                            section = next(row for row in rows if row.startswith("SECTION\t"))
                            self.assertIn(f"src/notes.md:{expected_line}\t", section)
                            self.assertTrue(section.endswith("\tReal / Other"), section)
                            self.assertIn(f"SOURCE_SHA256\t{hashlib.sha256(path.read_bytes()).hexdigest()}", rows)

    def test_unicode_separator_does_not_create_a_heading_or_metadata_field(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/notes.md", prompt("notes", "# Real\nfirst\u2028## False\nneedle\n", summary="First\u2028second"))
            catalog = m.discover(root)
            self.assertFalse(catalog.errors, catalog.errors)
            self.assertEqual(catalog.catalog["notes"][0].summary, "First\u2028second")
            graph = m.Graph(root)
            rows = m.search_lines(graph, query="needle")
            self.assertFalse(graph.errors, graph.errors)
            self.assertTrue(next(row for row in rows if row.startswith("SECTION\t")).endswith("\tReal"))

    def test_reference_lines_count_markdown_line_endings_only(self):
        owner = ROOT / "src/entry.md"
        for ending in ("\n", "\r\n", "\r"):
            with self.subTest(ending=repr(ending)):
                body = "first\u2028still first" + ending + "[real](leaf.md)" + ending
                refs = m.body_references(owner, body)
                self.assertEqual([(r.text, r.line) for r in refs], [("leaf.md", 2)])

    def test_search_ranks_section_evidence_then_whole_words_before_word_starts(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/a-metadata.md", prompt("metadata", "## First\nUnrelated.\n"
                  "## Second\nStill unrelated.\n", summary="vision account"))
            f.put("src/b-prefix.md", prompt("prefix", "## Change\nA visionary account."))
            f.put("src/c-direct.md", prompt("direct", "## Plan\nA vision account.", role="reference"))
            f.put("src/d-inner.md", prompt("inner", "## Change\nA revision account."))
            rows = m.search_lines(m.Graph(root), query="vision account", limit=1)
            self.assertIn("MATCHES\t4\tSHOWN\t1", rows)
            self.assertTrue(next(row for row in rows if row.startswith("SECTION\t"))
                            .startswith("SECTION\tdirect\t"))
            rows = m.search_lines(m.Graph(root), query="vision account", limit=10)
            identities = [row.split("\t")[1] for row in rows if row.startswith("SECTION\t")]
            self.assertEqual(identities, ["direct", "prefix", "metadata", "metadata"])

    def test_query_terms_match_word_starts_light_stems_and_cjk_pairs(self):
        def found(query, text):
            return [term.found(text.casefold()) for term in m.query_terms(query)]
        # Recorded false positives: 'gh' inside 'might', 'vision' inside 'revision'.
        self.assertEqual(found("gh", "it might need ghz"), [False])
        self.assertEqual(found("gh", "run gh auth"), [True])
        self.assertEqual(found("vision", "revision"), [False])
        self.assertEqual(found("vision", "visionary taste_vision"), [True])
        # Recorded misses: 'reflection' for core.memory.reflect, plurals for singulars.
        self.assertEqual(found("reflection agents", "core.memory.reflect: one agent"), [True, True])
        self.assertEqual(found("evening", "even"), [False])
        self.assertEqual(found("79", "item79"), [True])
        self.assertEqual(found("79", "179"), [False])
        # Chinese has no spaces: runs split from Latin text and into overlapping pairs.
        self.assertEqual([term.text for term in m.query_terms("Email周报 买咖啡机")],
                         ["email", "周报", "买咖", "咖啡", "啡机"])
        self.assertEqual(found("咖啡机", "二手咖啡机预算"), [True, True])
        self.assertEqual(found("coffee", "买coffee"), [True])

    def test_list_reads_document_text_only_when_no_summary_matches(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/printer.md", prompt("home.printer", "## Duplex\nReset the duplex unit.",
                                           summary="Printer maintenance"))
            f.put("src/scanner.md", prompt("home.scanner", "Shares a desk with the printer.",
                                           summary="Scanner setup"))
            graph = m.discover(root)
            rows = m.list_lines(graph, query="printers")
            self.assertIn("MATCHES\t1\tSHOWN\t1", rows)
            self.assertEqual(graph.body_reads, 0)
            self.assertFalse(any(row.startswith(("NOTE\t", "FILE_READS\t")) for row in rows))
            rows = m.list_lines(graph, query="duplex")
            self.assertIn("MATCHES\t1\tSHOWN\t1", rows)
            self.assertIn("FILE_READS\t4", rows)
            self.assertIn("NOTE\tNo identity or summary contains every term; "
                          "these documents do in their text (search shows where)", rows)
            self.assertTrue(rows[-1].startswith("home.printer\tsrc/printer.md\t"))

    def test_list_ranks_exact_words_before_stems_and_prefixes(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/a.md", prompt("a.reflect", summary="Revise memory from experience"))
            f.put("src/b.md", prompt("b.periodic", summary="Scheduled reflection over sessions"))
            rows = m.list_lines(m.discover(root), query="reflection")
            self.assertIn("MATCHES\t2\tSHOWN\t2", rows)
            self.assertEqual([row.split("\t")[0] for row in rows if row.startswith(("a.", "b."))],
                             ["b.periodic", "a.reflect"])

    def test_queries_without_a_full_match_return_labelled_majority_matches(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/a.md", prompt("a.trips", "## Bike\nA bike route and rain gear.", summary="Trips"))
            f.put("src/b.md", prompt("b.weather", "## Rain\nRain gear and umbrellas.", summary="Weather"))
            rows = m.list_lines(m.discover(root), query="bike rain helmet")
            self.assertIn("MATCHES\t1\tSHOWN\t1", rows)
            self.assertIn("NOTE\tNo document contains every term; these contain 2 of 3 query words; "
                          "found nowhere: helmet", rows)
            rows = m.search_lines(m.Graph(root), query="bike rain helmet")
            self.assertIn("NOTE\tNo section contains every term; these contain 2 of 3 query words; "
                          "found nowhere: helmet", rows)
            self.assertEqual([row.split("\t")[1] for row in rows if row.startswith("SECTION\t")],
                             ["a.trips"])
            # A two-term query needs both terms; the note still names the absent one.
            for rows in (m.list_lines(m.discover(root), query="rain helmet"),
                         m.search_lines(m.Graph(root), query="rain helmet")):
                self.assertTrue(any(row.startswith("MATCHES\t0\t") for row in rows))
                self.assertTrue(any(row.startswith("NOTE\t") and row.endswith("found nowhere: helmet")
                                    for row in rows))

    def test_majority_needs_more_than_half_of_the_query_words(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/two.md", prompt("two", "kappa sigma"))
            self.assertIn("MATCHES\t0\tSHOWN\t0",
                          m.search_lines(m.Graph(root), query="kappa sigma omega delta"))
            f.put("src/three.md", prompt("three", "kappa sigma omega"))
            rows = m.search_lines(m.Graph(root), query="kappa sigma omega delta")
            self.assertIn("MATCHES\t1\tSHOWN\t1", rows)
            self.assertTrue(any(row.startswith("SECTION\tthree\t") for row in rows))

    def test_a_cjk_run_counts_as_one_query_word(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/coffee.md", prompt("coffee", "## 咖啡机\n预算有限。"))
            # 咖啡机 alone is one of two words, so it does not satisfy a majority.
            self.assertIn("MATCHES\t0\tSHOWN\t0", m.search_lines(m.Graph(root), query="taste 咖啡机"))
            rows = m.search_lines(m.Graph(root), query="买咖啡机")
            self.assertIn("MATCHES\t1\tSHOWN\t1", rows)
            self.assertIn("NOTE\tNo section contains every term; these contain most of 买咖啡机", rows)

    def test_stems_complete_inflections_without_crossing_into_other_words(self):
        def found(query, text):
            return all(term.found(text.casefold()) for term in m.query_terms(query))
        for query, text in (("queries", "one query"), ("entries", "an entry"), ("reflection", "reflected"),
                            ("evaluation", "evaluate"), ("requirements", "required"),
                            ("classes", "class"), ("tests", "testing"), ("files", "file")):
            self.assertTrue(found(query, text), (query, text))
        for query, text in (("press", "present"), ("species", "specific"), ("series", "serious"),
                            ("basis", "basic"), ("container", "contains"), ("learner", "learning"),
                            ("experiment", "experience"), ("instrument", "instructions"),
                            ("complement", "complete"), ("notes", "notion"), ("cookies", "cooking")):
            self.assertFalse(found(query, text), (query, text))

    def test_query_words_drop_wrapping_and_punctuation_only_terms(self):
        self.assertEqual([term.text for term in m.query_terms('"human correction" (memory), taste - 「咖啡机」、日报')],
                         ["human", "correction", "memory", "taste", "咖啡", "啡机", "日报"])
        self.assertEqual([term.text for term in m.query_terms("C# .md --all")], ["c#", ".md", "--all"])

    def test_words_as_typed_rank_before_stems_and_excerpts_anchor_on_stems(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/a.md", prompt("a.stem", "## Notes\n" + "padding " * 40 + "They reflected.",
                                     summary="Reflected notes", primary=True))
            f.put("src/b.md", prompt("b.typed", "## Notes\nSeveral reflections.", summary="Reflections"))
            rows = m.search_lines(m.Graph(root), query="reflection", excerpt_chars=40)
            self.assertEqual([row.split("\t")[1] for row in rows if row.startswith("SECTION\t")],
                             ["b.typed", "a.stem"])
            self.assertIn("reflected", [row for row in rows if row.startswith("EXCERPT\t")][1])
            listed = m.list_lines(m.discover(root), query="reflection")
            self.assertEqual([row.split("\t")[0] for row in listed if row.startswith(("a.", "b."))],
                             ["b.typed", "a.stem"])
            f.put("src/a.md", prompt("a.stem", "They reflected.", summary="Notes", primary=True))
            f.put("src/b.md", prompt("b.typed", "Several reflections.", summary="Notes"))
            listed = m.list_lines(m.discover(root), query="reflection")
            self.assertTrue(any(row.startswith("NOTE\t") for row in listed))
            self.assertEqual([row.split("\t")[0] for row in listed if row.startswith(("a.", "b."))],
                             ["b.typed", "a.stem"])
            listed = m.list_lines(m.discover(root), query="reflection", primary_only=True)
            self.assertEqual([row.split("\t")[0] for row in listed if row.startswith(("a.", "b."))],
                             ["a.stem"])

    def test_list_text_fallback_ignores_metadata_headers(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/a.md", prompt("a.note", "Plain body.", role="reference"))
            for query in ("summary", "reference primary", "role"):
                self.assertIn("MATCHES\t0\tSHOWN\t0", m.list_lines(m.discover(root), query=query))

    def test_majority_matches_rank_rarer_terms_first(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/a.md", prompt("a.common", "## One\nsigma omega\n## Two\nsigma\n## Three\nomega"))
            f.put("src/b.md", prompt("b.rare", "## One\nkappa sigma"))
            rows = m.search_lines(m.Graph(root), query="kappa sigma omega")
            self.assertIn("MATCHES\t2\tSHOWN\t2", rows)
            self.assertEqual([row.split("\t")[1] for row in rows if row.startswith("SECTION\t")],
                             ["b.rare", "a.common"])

    def test_search_ranking_preserves_heading_context_identity_filters_and_stable_ties(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/a.md", prompt("topic.a", "## Vision\n### First\nMaya.\n### Second\nMaya."))
            f.put("src/b.md", prompt("topic.b", "## Vision\n### Third\nMaya."))
            rows = m.search_lines(m.Graph(root), query="vision Maya")
            sections = [row.split("\t") for row in rows if row.startswith("SECTION\t")]
            self.assertEqual([row[-1] for row in sections],
                             ["Vision / First", "Vision / Second", "Vision / Third"])
            rows = m.search_lines(m.Graph(root), query="topic.b vision Maya")
            self.assertIn("MATCHES\t1\tSHOWN\t1", rows)
            self.assertTrue(any(row.startswith("SECTION\ttopic.b\t") for row in rows))

    def test_search_direct_evidence_precedes_unrelated_children_of_matching_heading(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/a.md", prompt("outline", "# Vision\n\n## One\nUnrelated apples.\n"
                  "## Two\nUnrelated oranges.\n## Three\nUnrelated pears."))
            f.put("src/z.md", prompt("direct", "# Plan\nA vision for useful memory."))
            rows = m.search_lines(m.Graph(root), query="vision", limit=2)
            self.assertIn("MATCHES\t5\tSHOWN\t2", rows)
            self.assertEqual([row.split("\t")[1] for row in rows if row.startswith("SECTION\t")],
                             ["outline", "direct"])
            rows = m.search_lines(m.Graph(root), query="vision", limit=10)
            self.assertIn("MATCHES\t5\tSHOWN\t5", rows)
            self.assertTrue(any(row.endswith("\tVision / Three") for row in rows))

    def test_search_excerpt_keeps_identifier_witness_before_later_generic_whole_term(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/notes.md", prompt("coordination", "# Interrupted work\n"
                  "The timeout_ms value limits how long the caller waits, not the remote operation's lifetime.\n"
                  + "Reconcile the actual operation state before retrying. " * 12
                  + "A later human review assesses the reconciled result."))
            rows = m.search_lines(m.Graph(root), query="timeout review", excerpt_chars=140)
            excerpt = next(row for row in rows if row.startswith("EXCERPT\t"))
            self.assertIn("timeout_ms", excerpt)
            self.assertIn("operation's lifetime", excerpt)
            f.put("src/notes.md", prompt("coordination", "# Notes\n记忆方法 supplies context. "
                  + "Retain the useful context. " * 12 + "A human assesses the result."))
            rows = m.search_lines(m.Graph(root), query="记忆 human", excerpt_chars=80)
            self.assertTrue(any(row.startswith("EXCERPT\t") and "记忆方法" in row for row in rows))

    def test_search_prefers_whole_term_excerpt_and_keeps_unicode_substrings(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/a.md", prompt("partial", "## Notes\nStrassenbahn and 记忆方法."))
            f.put("src/z.md", prompt("whole", "## Notes\n" + "Strassenbahn " * 100
                  + "Straße crossing."))
            rows = m.search_lines(m.Graph(root), query="STRASSE", limit=1, excerpt_chars=60)
            self.assertIn("MATCHES\t2\tSHOWN\t1", rows)
            self.assertTrue(any(row.startswith("SECTION\twhole\t") for row in rows))
            self.assertTrue(any("Straße crossing" in row for row in rows))
            rows = m.search_lines(m.Graph(root), query="记忆", limit=1)
            self.assertIn("MATCHES\t1\tSHOWN\t1", rows)
            self.assertTrue(any("记忆方法" in row for row in rows))

    def test_search_retains_inline_code_and_literal_hashes_in_ancestor_headings(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/notes.md", prompt("notes", "## `Acme`\n### Contacts\nMaya\n"
                  "## C#\n### Contacts\nZoe\n## Closing hashes ###\n### Contacts\nAli\n"))
            for query, breadcrumb in (("Acme Maya", "`Acme` / Contacts"),
                                      ("C# Zoe", "C# / Contacts"),
                                      ("Closing hashes Ali", "Closing hashes / Contacts")):
                with self.subTest(query=query):
                    rows = m.search_lines(m.Graph(root), query=query)
                    self.assertIn("MATCHES\t1\tSHOWN\t1", rows)
                    self.assertTrue(any(row.startswith("SECTION\t") and row.endswith("\t" + breadcrumb)
                                        for row in rows), rows)

    def test_literal_comment_opener_in_code_does_not_hide_search_headings(self):
        with workspace() as root:
            f = Fixture(root)
            f.put('src/notes.md', prompt('notes',
                '`<!--`\n\n## Actual\nMaya\n\n<!--\n## Hidden\n-->\n'))
            rows = m.search_lines(m.Graph(root), query='Actual Maya')
            self.assertIn('MATCHES\t1\tSHOWN\t1', rows)
            self.assertTrue(any(row.startswith('SECTION\t') and row.endswith('\tActual') for row in rows), rows)

    def test_search_bounds_displayed_heading_without_truncating_searchable_ancestry(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/notes.md", prompt("notes", "## " + "x" * 20000 + " Acme\n"
                  "### Contacts\nMaya\n"))
            rows = m.search_lines(m.Graph(root), query="Acme Maya", limit=1, excerpt_chars=20)
            self.assertIn("MATCHES\t1\tSHOWN\t1", rows)
            section = next(row for row in rows if row.startswith("SECTION\t"))
            self.assertEqual(len(section.split("\t")[-1]), 240)
            self.assertTrue(section.endswith("…"))
            self.assertLess(len("\n".join(rows)), 1000)

    def test_search_centers_unicode_casefold_matches_on_original_text(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/notes.md", prompt("notes", "# Topic\n" + "ß" * 400
                  + " needle trailing context"))
            rows = m.search_lines(m.Graph(root), query="needle", excerpt_chars=80)
            self.assertIn("MATCHES\t1\tSHOWN\t1", rows)
            excerpt = next(row for row in rows if row.startswith("EXCERPT\t"))
            self.assertIn("needle trailing context", excerpt)
            self.assertLessEqual(len(excerpt.partition("\t")[2]), 82)
            f.put("src/notes.md", prompt("notes", "# Topic\n" + "İ" * 400
                  + " Straße trailing context"))
            rows = m.search_lines(m.Graph(root), query="STRASSE", excerpt_chars=80)
            self.assertIn("MATCHES\t1\tSHOWN\t1", rows)
            self.assertTrue(any("Straße trailing context" in row for row in rows))

    def test_search_reports_bad_metadata_and_duplicate_identity_without_loading_links(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/bad.md", "---\nid: broken\nrole: random\n---\nneedle\n")
            f.put("src/copy.md", prompt("beta.leaf", "needle"))
            f.put("src/good.md", prompt("good", "needle [missing](absent.md)", role="reference"))
            graph = m.Graph(root)
            rows = m.search_lines(graph, query="needle")
            self.assertTrue(any("role must" in row for row in rows))
            self.assertTrue(any("duplicate id" in row for row in rows))
            self.assertTrue(any(row.startswith("SECTION\tgood\t") for row in rows))
            self.assertNotIn(root / "src/absent.md", graph.contents)

    def test_search_snapshot_identifies_captured_bytes_after_file_changes(self):
        with workspace() as root:
            f = Fixture(root)
            path = f.put("src/note.md", prompt("note", "# Earlier\nneedle"))
            graph = m.Graph(root)
            original = m.search_lines(graph, query="needle")
            f.put("src/note.md", prompt("note", "# Later\nneedle modified"))
            self.assertEqual(original, m.search_lines(graph, query="needle"))
            fresh = m.search_lines(m.Graph(root), query="needle")
            self.assertNotEqual(original, fresh)
            self.assertIn(f"SOURCE_SHA256\t{hashlib.sha256(path.read_bytes()).hexdigest()}", fresh)

    def test_search_cli_requires_bounded_query_and_reports_data_role(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/notes.md", prompt("notes", "# Printer\nDuplex setting.", role="reference"))
            command = [sys.executable, str(ROOT / "tools/modules.py"), "search", "--root", str(root)]
            result = subprocess.run(command + ["--query", "duplex", "--limit", "1"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("MATCHES\t1\tSHOWN\t1", result.stdout)
            self.assertIn("\treference\tPrinter", result.stdout)
            for options in ([], ["--query", "duplex", "--all"],
                            ["--query", "duplex", "--primary-only"]):
                result = subprocess.run(command + options, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)

    def test_colocated_suite_discovers_only_its_entry_and_keeps_rubric_out_of_runtime(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/nested/tests/README.md", prompt("beta.tests",
                  "Evaluator rubric. Use [case](workspace/case.md) as data.",
                  summary="Test leaf behavior under incomplete evidence"))
            case = f.put("src/nested/tests/workspace/case.md", "# Frozen case\nObserved partial outcome.\n")
            catalog = m.discover(root)
            self.assertFalse(catalog.errors, catalog.errors)
            self.assertEqual(set(catalog.catalog), {"alpha.root", "beta.leaf", "beta.tests"})
            self.assertNotIn("beta.tests\t", "\n".join(m.list_lines(catalog, primary_only=True)))
            graph, ordinary = f.assemble(seeds=(m.Seed("src/nested/leaf.md", "ordinary task"),))
            self.assertPass(ordinary)
            self.assertNotIn(case, graph.contents)
            self.assertNotIn(root / "src/nested/tests/README.md", graph.contents)
            graph, evidence = f.assemble(seeds=(m.Seed("beta.tests", "evaluator selects evidence", "support"),))
            self.assertPass(evidence)
            self.assertEqual([item.module_id for item in evidence.modules], ["alpha.root"])
            self.assertNotIn(case, graph.contents)  # data does not traverse its links
            _, selected = f.assemble(seeds=(m.Seed(str(case.relative_to(root)), "permitted fixture", "support"),))
            self.assertPass(selected)
            self.assertIn(case, selected.support)

    def test_headerless_support_is_checked_as_data_but_cannot_be_a_prompt(self):
        with workspace() as root:
            f = Fixture(root)
            path = "src/nested/note.md"
            f.put(path, "# Partial episode\nNo explanation yet.\n")
            self.assertFalse(m.load_graph(root).errors)
            _, activated = f.assemble(seeds=(m.Seed(path, "mistaken prompt selection"),))
            self.assertIn("missing frontmatter", "\n".join(activated.errors))
            f.put(path, "# Partial episode\n[unavailable](missing.md)\n")
            self.assertIn("missing link", "\n".join(m.load_graph(root).errors))

    def test_frontmatter_opts_into_strict_metadata_even_in_a_support_directory(self):
        with workspace() as root:
            f = Fixture(root)
            path = "src/nested/tests/workspace/data.md"
            f.put(path, "---\ntitle: ordinary YAML document\n---\n# Data\n")
            self.assertTrue(m.discover(root).errors)
            self.assertTrue(m.load_graph(root).errors)
            _, as_data = f.assemble(seeds=(m.Seed(path, "explicit data capture", "support"),))
            self.assertPass(as_data)

    def test_discovery_stops_reading_at_closing_header(self):
        with workspace() as root:
            Fixture(root)
            original_open = Path.open

            class HeaderGuard:
                def __init__(self, source):
                    self.source = source
                    self.delimiters = 0
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    self.source.close()
                def __iter__(self):
                    return self
                def __next__(self):
                    if self.delimiters == 2:
                        raise AssertionError("read past header")
                    row = next(self.source)
                    self.delimiters += row.strip() == "---"
                    return row

            def guarded(path, *args, **kwargs):
                return HeaderGuard(original_open(path, *args, **kwargs))

            with patch.object(Path, "open", guarded):
                graph = m.discover(root)
            self.assertFalse(graph.errors)
            self.assertEqual(graph.metadata_reads, 2)

    def test_markdown_links_titles_references_anchors_and_code(self):
        with workspace() as root:
            f = Fixture(root)
            leaf = root / "src/nested/leaf.md"
            tick = chr(96)
            body = (
                '[inline](nested/leaf.md#part "title")\n'
                '[spaces](<nested/with space.md>)\n'
                '[nested](nested/a(b(c)).md)\n'
                '[escaped](nested/a\\(b\\).md)\n'
                '[full][ref] [ref][] [ref]\n\n'
                '[ref]: nested/leaf.md\n'
                f'{tick}[code](ignored.md){tick}\n'
                f'{tick * 3}markdown\n[fenced](ignored.md)\n{tick * 3}\n'
                '[external](https://example.org/a.md) ![image](ignored.png)\n'
                '![image reference][ref] ![ref][] ![ref]\n'
            )
            refs = m.body_references(root / "src/entry.md", body)
            self.assertEqual(len(refs), 7)
            self.assertEqual(sum(ref.target == leaf for ref in refs), 4)
            self.assertIn(root / "src/nested/with space.md", {ref.target for ref in refs})
            self.assertIn(root / "src/nested/a(b(c)).md", {ref.target for ref in refs})

    def test_excluded_markdown_regions_cannot_declare_dependencies(self):
        bodies = (
            "    [example](nested/leaf.md)\n",
            "\t[example](nested/leaf.md)\n",
            "<!-- [example](nested/leaf.md) -->",
            "<div>\n[example](nested/leaf.md)\n</div>",
            "<script>[example](nested/leaf.md)</script>",
            '<span title="[example](nested/leaf.md)">label</span>',
            "<https://example.org/[example](nested/leaf.md)>",
        )
        for body in bodies:
            with self.subTest(body=body), workspace() as root:
                f = Fixture(root)
                f.put("src/entry.md", prompt("alpha.root", body))
                refs = m.body_references(root / "src/entry.md", body)
                self.assertEqual(refs, ())
                _, assembly = f.assemble(m.Selection("alpha.root", "nested/leaf.md", "example"))
                self.assertTrue(assembly.errors)

    def test_balanced_empty_and_escaped_labels_remain_links(self):
        with workspace() as root:
            Fixture(root)
            body = (
                "Use [an [inner] label](nested/leaf.md).\n"
                "Use [an escaped \\] label](nested/leaf.md).\n"
                "Use [](nested/leaf.md).\n"
                "Use [spaces](<with space.md>).\n"
                "- A list item\n\n"
                "    [a continuation](nested/leaf.md)\n"
                "\n[wrapped][ref]\n\n[ref]:\n    nested/leaf.md\n"
            )
            refs = m.body_references(root / "src/entry.md", body)
            self.assertEqual(len(refs), 6)
            self.assertEqual({ref.target for ref in refs},
                             {root / "src/nested/leaf.md", root / "src/with space.md"})

    def test_fenced_examples_inside_containers_are_not_dependencies(self):
        cases = {
            "quote": "> ~~~md\n> [example](missing.md)\n> ~~~\n",
            "nested quote": "> > ~~~\n> > [example](missing.md)\n> > ~~~\n",
            "wide list": "100. example\n\n     ~~~md\n     [example](missing.md)\n     ~~~\n",
            "list opener": "- ~~~\n  [example](missing.md)\n  ~~~\n",
            "list quote": "- > ~~~\n  > [example](missing.md)\n  > ~~~\n",
            "quote list": "> 100. ~~~\n>      [example](missing.md)\n>      ~~~\n",
        }
        with workspace() as root:
            f = Fixture(root)
            for name, example in cases.items():
                with self.subTest(name=name):
                    body = example + "\n[active](nested/leaf.md)\n"
                    f.put("src/entry.md", prompt("alpha.root", body))
                    refs = m.body_references(root / "src/entry.md", body)
                    self.assertEqual([(r.text, r.line) for r in refs],
                                     [("nested/leaf.md", example.count("\n") + 2)])
                    self.assertPass(f.assemble(m.Selection(
                        "alpha.root", "nested/leaf.md", "real prose"))[1])

    def test_unclosed_fences_end_at_their_container_boundary(self):
        cases = (
            "> ~~~\n> [example](missing.md)\n[active](kept.md)\n",
            "- ~~~\n  [example](missing.md)\n[active](kept.md)\n",
            "- ~~~\n  [example](missing.md)\n- [active](kept.md)\n",
            "> > ~~~\n> > [example](missing.md)\n> [active](kept.md)\n",
        )
        for body in cases:
            with self.subTest(body=body):
                refs = m.body_references(ROOT / "src/entry.md", body)
                self.assertEqual([(r.text, r.line) for r in refs], [("kept.md", 3)])

    def test_container_prose_and_literal_fence_contents_keep_their_roles(self):
        cases = (
            "> [active](kept.md)\n",
            "100. paragraph\n\n     [active](kept.md)\n",
            "100. paragraph\nlazy continuation\n     ~~~\n     [example](missing.md)\n     ~~~\n[active](kept.md)\n",
            "~~~\n> ~~~\n[example](missing.md)\n- ~~~\n~~~\n[active](kept.md)\n",
            "> ~~~~\n> ~~~\n> [example](missing.md)\n> ~~~~\n> [active](kept.md)\n",
        )
        for body in cases:
            with self.subTest(body=body):
                refs = m.body_references(ROOT / "src/entry.md", body)
                line = next(i for i, row in enumerate(body.splitlines(), 1) if "[active]" in row)
                self.assertEqual([(r.text, r.line) for r in refs], [("kept.md", line)])

    def test_empty_list_item_ends_on_its_second_blank_line(self):
        cases = (
            ("-\n\n    ~~~\n  [active](kept.md)\n", [("kept.md", 4)]),
            ("-\n\n  ~~~\n[example](missing.md)\n", []),
            ("> -\n>\n>     ~~~\n>   [active](kept.md)\n", [("kept.md", 4)]),
            ("-   \n  ~~~\n[active](kept.md)\n", [("kept.md", 3)]),
            ("- parent\n\n    ~~~\n    [example](missing.md)\n    ~~~\n[active](kept.md)\n",
             [("kept.md", 6)]),
        )
        for body, expected in cases:
            with self.subTest(body=body):
                refs = m.body_references(ROOT / "src/entry.md", body)
                self.assertEqual([(r.text, r.line) for r in refs], expected)

    def test_new_list_after_container_exit_uses_the_surviving_parent(self):
        for opening in ("> > paragraph", "> - paragraph", "- paragraph"):
            with self.subTest(opening=opening):
                body = opening + "\n2. item\n    ~~~\n    [example](missing.md)\n    ~~~\n[active](kept.md)\n"
                refs = m.body_references(ROOT / "src/entry.md", body)
                self.assertEqual([(r.text, r.line) for r in refs], [("kept.md", 6)])
        # The same marker cannot interrupt a paragraph within its current container.
        for prefix in ("", "> "):
            with self.subTest(prefix=prefix):
                body = "\n".join(prefix + row for row in (
                    "paragraph", "2. text", "    ~~~", "    [active](kept.md)", "    ~~~"))
                refs = m.body_references(ROOT / "src/entry.md", body)
                self.assertEqual([(r.text, r.line) for r in refs], [("kept.md", 4)])

    def test_code_spans_cannot_cross_markdown_block_boundaries(self):
        cases = (
            "`open\n\n[active](nested/leaf.md)\n\nclose`\n",
            "`open\n# [active](nested/leaf.md)\nclose`\n",
            "# `open\n[active](nested/leaf.md)\nclose`\n",
            "`open\n---\n[active](nested/leaf.md)\nclose`\n",
            "`open\n===\n[active](nested/leaf.md)\nclose`\n",
            "`open\n***\n[active](nested/leaf.md)\nclose`\n",
            "- `open\n- [active](nested/leaf.md)\n- close`\n",
            "`open\n> [active](nested/leaf.md)\nclose`\n",
            "> `open\n>\n> [active](nested/leaf.md)\n> close`\n",
            "`open\n~~~\nexample\n~~~\n[active](nested/leaf.md)\nclose`\n",
            "`open\n<div>example</div>\n\n[active](nested/leaf.md)\nclose`\n",
        )
        with workspace() as root:
            f = Fixture(root)
            for body in cases:
                with self.subTest(body=body):
                    line = next(i for i, row in enumerate(body.splitlines(), 1) if "[active]" in row)
                    refs = m.body_references(root / "src/entry.md", body)
                    self.assertEqual([(r.text, r.line) for r in refs], [("nested/leaf.md", line)])
                    f.put("src/entry.md", prompt("alpha.root", body))
                    self.assertPass(f.assemble(m.Selection(
                        "alpha.root", "nested/leaf.md", "real link outside code"))[1])
                    f.put("src/entry.md", prompt("alpha.root", body.replace("nested/leaf.md", "missing.md")))
                    self.assertIn("missing link", "\n".join(m.load_graph(root).errors))

    def test_multiline_code_spans_stay_inside_their_leaf_block(self):
        cases = (
            "`open\n[example](missing.md)\nclose`\n",
            "> `open\n> [example](missing.md)\n> close`\n",
            "> `open\n[example](missing.md)\nclose`\n",
            "- `open\n  [example](missing.md)\n  close`\n",
            "- `open\n[example](missing.md)\nclose`\n",
            "`open\n    [example](missing.md)\nclose`\n",
            "``open `[example](missing.md)` close``\n",
            "`open [example](missing.md) \\`\n",
        )
        for body in cases:
            with self.subTest(body=body):
                self.assertEqual(m.body_references(ROOT / "src/entry.md", body), ())

    def test_backslash_parity_controls_link_and_code_openers(self):
        cases = (
            (r"\[active](kept.md)", []),
            (r"\\[active](kept.md)", ["kept.md"]),
            (r"\\\[active](kept.md)", []),
            (r"\\\\[active](kept.md)", ["kept.md"]),
            (r"\![active](kept.md)", ["kept.md"]),
            (r"\\![image](ignored.md)", []),
            (r"\`[active](kept.md)`", ["kept.md"]),
            (r"\\`[example](ignored.md)`", []),
            (r"\``[example](ignored.md)`", []),
            (r"\```[example](ignored.md)``", []),
        )
        for body, expected in cases:
            with self.subTest(body=body):
                self.assertEqual([r.text for r in m.body_references(ROOT / "src/entry.md", body)], expected)

    def test_html_blocks_use_their_own_termination_rules(self):
        cases = (
            ("<div>example</div>\n[example](ignored.md)\n\n[active](kept.md)\n", 4),
            ("</div>\n[example](ignored.md)\n\n[active](kept.md)\n", 4),
            ("<hr>\n[example](ignored.md)\n\n[active](kept.md)\n", 4),
            ("<span>\n[example](ignored.md)\n</span>\n\n[active](kept.md)\n", 5),
            ("<custom-tag enabled data-x='a'>\n[example](ignored.md)\n\n[active](kept.md)\n", 4),
            ("<script>\n\n[example](ignored.md)\n</script>\n[active](kept.md)\n", 5),
            ("<style>example</style>\n[active](kept.md)\n", 2),
            ("> <div>\n> [example](ignored.md)\n[active](kept.md)\n", 3),
            ("- <span>\n  [example](ignored.md)\n[active](kept.md)\n", 3),
            ("A paragraph\n<span>\n[active](kept.md)\n", 3),
        )
        for body, line in cases:
            with self.subTest(body=body):
                self.assertEqual([(r.text, r.line) for r in m.body_references(ROOT / "src/entry.md", body)],
                                 [("kept.md", line)])

    def test_inline_link_titles_whitespace_and_empty_targets(self):
        owner = ROOT / "src/entry.md"
        cases = (
            ("[self]()", owner), ("[]()", owner),
            ("[self]( \t\n )", owner),
            ('[note](leaf.md "caption\\\" quoted")', owner.parent / "leaf.md"),
            ("[note](leaf.md 'caption\\' quoted')", owner.parent / "leaf.md"),
            (r"[note](leaf.md (caption\) quoted))", owner.parent / "leaf.md"),
            ('[note](leaf.md "two\nlines")', owner.parent / "leaf.md"),
            ('[note](leaf.md\u00a0"caption")', owner.parent / 'leaf.md\u00a0"caption"'),
            ('[note](leaf.md\u2003name)', owner.parent / 'leaf.md\u2003name'),
        )
        for body, target in cases:
            with self.subTest(body=body):
                self.assertEqual([r.target for r in m.body_references(owner, body)], [target])

    def test_link_destinations_and_titles_own_their_inline_punctuation(self):
        cases = (
            ('[first](a`b.md) [after](kept.md) `tail`', ['a`b.md', 'kept.md']),
            ('[first](<a`b.md>) [after](kept.md) `tail`', ['a`b.md', 'kept.md']),
            ('[first](leaf.md "a`b") [after](kept.md) `tail`', ['leaf.md', 'kept.md']),
            ('[first](leaf.md "<!--") [after](kept.md) <!-- -->', ['leaf.md', 'kept.md']),
            ('[first](a<?b.md) [after](kept.md) <?end?>', ['a<?b.md', 'kept.md']),
            ('`[hidden](missing.md)` [first](a`b.md) [after](kept.md) `tail`',
             ['a`b.md', 'kept.md']),
        )
        for body, expected in cases:
            with self.subTest(body=body):
                self.assertEqual([r.text for r in m.body_references(ROOT / 'src/entry.md', body)], expected)

    def test_code_html_and_autolinks_consume_each_other_only_in_source_order(self):
        cases = (
            '`<!--` [active](kept.md) <!-- -->',
            'A <span title="`[hidden](missing.md)"> [active](kept.md) `tail`',
            'A <!-- ` [hidden](missing.md) --> [active](kept.md) `tail`',
            '<https://example.org/`[hidden](missing.md)> [active](kept.md) `tail`',
            '<x`y@example.org> [active](kept.md) `tail`',
            'A <? ` [hidden](missing.md) ?> [active](kept.md) `tail`',
            'A <![CDATA[ ` [hidden](missing.md) ]]> [active](kept.md) `tail`',
            '`<span title="` [active](kept.md)',
        )
        for body in cases:
            with self.subTest(body=body):
                self.assertEqual([r.text for r in m.body_references(ROOT / 'src/entry.md', body)], ['kept.md'])

    def test_html_block_delimiters_and_inline_literals_have_separate_lifetimes(self):
        cases = (
            ('<!--\n\n[hidden](missing.md)\n-->\n[active](kept.md)', 5),
            ('<!-- done --> [hidden](missing.md)\n[active](kept.md)', 2),
            ('<?\n\n[hidden](missing.md)\n?>\n[active](kept.md)', 5),
            ('<!lowercase\n[hidden](missing.md)>\n[active](kept.md)', 3),
            ('<![CDATA[\n\n[hidden](missing.md)\n]]>\n[active](kept.md)', 5),
            ('<script>\n[hidden](missing.md)\n</pre>\n[active](kept.md)', 4),
            ('<script/>\n[active](kept.md)', 2),
            ('> <!--\n> [hidden](missing.md)\n[active](kept.md)', 3),
        )
        for body, line in cases:
            with self.subTest(body=body):
                refs = m.body_references(ROOT / 'src/entry.md', body)
                self.assertEqual([(r.text, r.line) for r in refs], [('kept.md', line)])

    def test_literal_backtick_destination_is_selected_and_captured(self):
        with workspace() as root:
            f = Fixture(root)
            destination = 'nested/literal`tick.md'
            content = prompt('literal.tick', 'A dependency with literal filename punctuation.')
            f.put('src/' + destination, content)
            f.put('src/entry.md', prompt('alpha.root',
                '[literal](' + destination + ') [leaf](nested/leaf.md) `tail`'))
            graph, result = f.assemble(m.Selection('alpha.root', destination, 'real linked input'))
            self.assertPass(result)
            self.assertIn((root / 'src' / destination).resolve(), graph.contents)
            self.assertEqual(graph.contents[(root / 'src' / destination).resolve()], content.encode())

    def test_unclosed_inline_html_keeps_literal_links_and_other_constructs(self):
        cases = (
            'prefix <!-- [a](a.md) <!-- [b](b.md)',
            'prefix <? [a](a.md) <? [b](b.md)',
            'prefix <!A [a](a.md) <!B [b](b.md)',
            'prefix <![CDATA[ [a](a.md) <![CDATA[ [b](b.md)',
            'prefix <? [a](a.md) <!-- [hidden](missing.md) --> [b](b.md)',
            'prefix <!-- [a](a.md)\n\n<!-- [hidden](missing.md) -->\n[b](b.md)',
            '[x][' * 2000 + ' [a](a.md) [b](b.md)',
        )
        for body in cases:
            with self.subTest(body=body[:100]):
                self.assertEqual([r.text for r in m.body_references(ROOT / 'src/entry.md', body)], ['a.md', 'b.md'])

    def test_invalid_inline_links_do_not_create_dependencies(self):
        owner = ROOT / "src/entry.md"
        cases = (
            '[note](<leaf.md>"caption")', '[note](<leaf.md>(caption))',
            '[note](leaf.md "two\n\nblocks")',
            '[note](leaf.md "two\n \t\nblocks")',
            '[note](leaf.md (nested (caption)))',
            '[note](leaf(a b))', '[note](leaf(a\tb))',
            '[note](leaf\\\nname)', '[note](\n\nleaf.md)',
            '[note](leaf.md\n\n)',
        )
        for body in (*cases, *('[note](leaf' + chr(c) + 'name)' for c in [0, 1, 9, 11, 12, 31, 127])):
            with self.subTest(body=body):
                self.assertEqual(m.body_references(owner, body), ())

    def test_invalid_inline_link_can_leave_a_shortcut_reference(self):
        owner = ROOT / "src/entry.md"
        for body in ('[note](not a link)', '[note](<leaf.md>"caption")',
                     '[note](\n\nleaf.md)'):
            with self.subTest(body=body):
                refs = m.body_references(owner, body + '\n\n[note]: fallback.md\n')
                self.assertEqual([(r.text, r.line) for r in refs], [('fallback.md', 1)])
        refs = m.body_references(owner, '[note]()\n\n[note]: fallback.md\n')
        self.assertEqual([r.target for r in refs], [owner])

    def test_reference_definitions_use_complete_syntax_at_paragraph_start(self):
        owner = ROOT / "src/entry.md"
        valid = (
            '[note]: leaf.md\n',
            '[note]: <with space.md> "a title"\n',
            '[note]:\n  leaf.md\n  "wrapped title"\n',
            '[note]: leaf.md "title\ncontinuation"\n',
            '[note]: leaf.md\n"bad title" trailing prose\n',
            '# heading\n[note]: leaf.md\n',
            '[first]: other.md\n    [note]: leaf.md\n',
        )
        for definition in valid:
            with self.subTest(definition=definition):
                refs = m.body_references(owner, definition + '\n[note]\n')
                self.assertEqual(len(refs), 1)
                self.assertEqual(refs[0].line, definition.count('\n') + 2)
        invalid = (
            'prose\n[note]: leaf.md\n',
            '[note]: <leaf.md>(title)\n',
            '[note]: leaf.md "title" trailing prose\n',
            '[note]: leaf.md "title\n\ncontinuation"\n',
            '[note]: leaf.md "unterminated\n',
            '    [note]: leaf.md\n',
            '# [note]: leaf.md\n',
        )
        for definition in invalid:
            with self.subTest(definition=definition):
                self.assertEqual(m.body_references(owner, definition + '\n[note]\n'), ())

    def test_reference_labels_keep_raw_spelling_and_reject_unescaped_brackets(self):
        owner = ROOT / "src/entry.md"
        for label in (r'an escaped\] bracket', r'an escaped\[ bracket', '`code`',
                      'two\n  lines', 'name &amp; value', 'x' * 999):
            with self.subTest(label=label):
                body = '[' + label + ']: leaf.md\n\n[' + label + ']\n'
                self.assertEqual([r.text for r in m.body_references(owner, body)], ['leaf.md'])
        for label in ('', ' ', 'un[escaped', 'un]escaped', 'x' * 1000):
            with self.subTest(label=label):
                body = '[' + label + ']: missing.md\n\n[' + label + ']\n'
                self.assertEqual(m.body_references(owner, body), ())
        # Definitions are block syntax: a code span does not erase their label,
        # destination or title, and the first complete definition still wins.
        body = '[note]: `first`.md "[literal](missing.md)"\n[note]: second.md\n\n[note]'
        self.assertEqual([r.text for r in m.body_references(owner, body)], ['`first`.md'])

    def test_container_definitions_are_global_and_preserve_destination_tabs(self):
        owner = ROOT / "src/entry.md"
        for definition in ('> [note]: leaf.md\n', '- [note]: leaf.md\n',
                           '> - [note]:\n>     leaf.md\n',
                           '>\t[note]: <a\tb.md>\n'):
            with self.subTest(definition=definition):
                refs = m.body_references(owner, '[note]\n\n' + definition)
                self.assertEqual(len(refs), 1)
                self.assertEqual(refs[0].line, 1)
                self.assertEqual(refs[0].target,
                                 owner.parent / ('a\tb.md' if '<a' in definition else 'leaf.md'))

    def test_deep_destination_parentheses_and_malformed_recovery(self):
        owner = ROOT / "src/entry.md"
        nested = "a(" * 256 + "leaf.md" + ")" * 256
        body = "[deep](" + nested + ") [escaped](a\\(b)"
        refs = m.body_references(owner, body)
        self.assertEqual([r.text for r in refs], [nested, r"a\(b"])
        # A failed destination must not consume a later real link or cross
        # whitespace to obtain a parenthesis that would make it valid.
        body = "[x](" * 4096 + " [good](leaf.md)"
        self.assertEqual([r.text for r in m.body_references(owner, body)], ["leaf.md"])
        body = "[bad](a(b c)) [good](leaf.md)"
        self.assertEqual([r.text for r in m.body_references(owner, body)], ["leaf.md"])

    def test_nested_links_select_inner_links_and_exclude_image_descriptions(self):
        owner = ROOT / "src/entry.md"
        cases = (
            ('[outer [inner](a.md)](b.md)', ['a.md']),
            ('[outer [inner](a.md)][ref]', ['a.md', 'b.md']),
            ('[[ref]]', ['b.md']),
            ('[outer [plain] text](b.md)', ['b.md']),
            ('![image [inner](a.md)](b.png)', []),
            ('[![image](a.png)](b.md)', ['b.md']),
            ('[outer [remote](https://example.org)](b.md)', []),
            ('[ref][undefined]', []),
            ('[ref](invalid title)', ['b.md']),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                body = text + '\n\n[ref]: b.md\n'
                self.assertEqual([r.text for r in m.body_references(owner, body)], expected)

    def test_link_brackets_cannot_cross_leaf_blocks(self):
        owner = ROOT / "src/entry.md"
        for body in ('[text\n\ncontinued](missing.md)',
                     '# [heading\nparagraph](missing.md)',
                     '[paragraph\n# heading](missing.md)',
                     '- [first\n- second](missing.md)',
                     '[text\n~~~\ncode\n~~~\ncontinued](missing.md)'):
            with self.subTest(body=body):
                self.assertEqual(m.body_references(owner, body), ())
        refs = m.body_references(owner, '> [wrapped\n> label](leaf.md)\n')
        self.assertEqual([(r.text, r.line) for r in refs], [('leaf.md', 1)])

    def test_reference_definition_syntax_reaches_validation_and_assembly(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/leaf.md", prompt("gamma.reference", "Shared understanding."))
            body = '[use][note]\n\n> [note]: leaf.md "description"\n'
            f.put("src/entry.md", prompt("alpha.root", body))
            self.assertFalse(m.load_graph(root).errors)
            self.assertPass(f.assemble(m.Selection("alpha.root", "leaf.md", "scoped reference"))[1])
            f.put("src/entry.md", prompt("alpha.root", '[note]\n\n[note]: <missing.md>(bad)'))
            self.assertFalse(m.load_graph(root).errors)
            self.assertTrue(f.assemble(m.Selection("alpha.root", "missing.md", "invalid syntax"))[1].errors)

    def test_destination_entities_decode_once_before_url_resolution(self):
        cases = (
            ("f&ouml;&ouml;.md", "föö.md"),
            ("a&amp;b.md", "a&b.md"),
            ("a&#38;b.md", "a&b.md"),
            ("a&#x26;b.md", "a&b.md"),
            (r"a\&amp;b.md", "a&amp;b.md"),
            ("a&amp;amp;b.md", "a&amp;b.md"),
            ("a%26amp;b.md", "a&amp;b.md"),
            ("a&notanentity;.md", "a&notanentity;.md"),
            ("a&amp.md", "a&amp.md"),
            ("a&bsol;_b.md", "a\\_b.md"),
            ("a&#35;section", "a"),
            ("a&NewLine;b.md", "a\nb.md"),
            ("a&#11;b.md", "a\x0bb.md"),
            ("a&#128;b.md", "a€b.md"),
            ("a&#xFFFF;b.md", "a\uffffb.md"),
            ("a&#0;b.md", "a\ufffdb.md"),
            ("a&#xD800;b.md", "a\ufffdb.md"),
            ("a&#1114112;b.md", "a\ufffdb.md"),
        )
        owner = ROOT / "src/entry.md"
        for destination, expected in cases:
            with self.subTest(destination=destination):
                self.assertEqual(m.local_target(owner, destination), (owner.parent / expected).resolve())
        self.assertIsNone(m.local_target(owner, "https&colon;//example.com/file.md"))

    def test_angle_destinations_validate_delimiters_and_keep_spaces(self):
        owner = ROOT / "src/entry.md"
        for body in ("[x](<a\nb.md>)", "[x](<a<b.md>)", r"[x](<a\>)",
                     "[x]\n\n[x]: <a\nb.md>"):
            with self.subTest(body=body):
                self.assertEqual(m.body_references(owner, body), ())
        for body, expected in (("[x](< leading.md>)", " leading.md"),
                               (r"[x](<a\>b.md>)", "a>b.md"),
                               (r"[x](<a\<b.md>)", "a<b.md"),
                               ("[x]\n\n[x]: <a\\>b.md>", "a>b.md"),
                               ("[x]\n\n[x]:\n  leaf.md", "leaf.md")):
            with self.subTest(body=body):
                self.assertEqual([r.target for r in m.body_references(owner, body)],
                                 [(owner.parent / expected).resolve()])

    def test_encoded_destinations_reach_actual_files_in_check_and_assembly(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/notes&facts.md", prompt("beta.notes", "Shared understanding."))
            for body in ("[notes](notes&amp;facts.md)",
                         "[notes]\n\n[notes]: notes&#38;facts.md"):
                with self.subTest(body=body):
                    f.put("src/entry.md", prompt("alpha.root", body))
                    self.assertFalse(m.load_graph(root).errors)
                    self.assertPass(f.assemble(m.Selection(
                        "alpha.root", "notes&amp;facts.md", "entity-spelled local file"))[1])
                    f.put("src/entry.md", prompt("alpha.root", body.replace("notes", "missing")))
                    self.assertIn("missing link", "\n".join(m.load_graph(root).errors))

    def test_cycles_and_selection_order_have_deterministic_output(self):
        with workspace() as root:
            f = Fixture(root)
            f.put("src/nested/leaf.md", prompt("beta.leaf", "Use [entry](../entry.md) when needed."))
            choices = [m.Selection("alpha.root", "nested/leaf.md", "forward"),
                       m.Selection("beta.leaf", "../entry.md", "back")]
            first_graph, first = f.assemble(*choices)
            other_graph, other = f.assemble(*reversed(choices))
            self.assertPass(first)
            self.assertEqual(m.assembly_lines(first_graph, first), m.assembly_lines(other_graph, other))
            self.assertEqual(len(first.modules), 2)

    def test_snapshot_covers_bootstrap_prompt_and_support(self):
        with workspace() as root:
            f = Fixture(root)
            graph, before = f.assemble()
            for path, text in (
                ("AGENTS.md", (root / "AGENTS.md").read_text() + "\nNew instruction.\n"),
                ("tools/router.py", "# changed helper\n"),
                ("src/entry.md", prompt("alpha.root", "new direction")),
            ):
                f.put(path, text)
            after = m.assemble(graph)
            self.assertEqual(before.digest, after.digest)
            self.assertNotEqual(before.digest, m.assemble(f.graph()).digest)

    def test_unrelated_change_does_not_change_active_digest(self):
        with workspace() as root:
            f = Fixture(root)
            _, before = f.assemble()
            f.put("src/unrelated.md", prompt("unused", "new memory"))
            _, after = f.assemble()
            self.assertEqual(before.digest, after.digest)

    def test_bootstrap_directive_cardinality_and_targets(self):
        for text in (
            "", "Mnemorph root: src/entry.md\n",
            "Mnemorph root: ../outside.md\nMnemorph narrow route: requested module\n"
            "Mnemorph support: tools/router.py\n",
            "Mnemorph root: src/entry.md\nMnemorph root: src/entry.md\n"
            "Mnemorph narrow route: requested module\nMnemorph support: tools/router.py\n",
        ):
            with self.subTest(text=text), workspace() as root:
                f = Fixture(root)
                f.put("AGENTS.md", text)
                self.assertTrue(m.assemble(f.graph()).errors)

    def test_reference_reach_does_not_enumerate_all_diamond_paths(self):
        with workspace() as root:
            f = Fixture(root)
            for depth in range(18):
                for side in ("a", "b"):
                    body = "" if depth == 17 else (
                        f"[left](./{depth + 1}a.md) [right](./{depth + 1}b.md)")
                    f.put(f"src/{depth}{side}.md", prompt(f"node.{depth}.{side}", body))
            f.put("src/entry.md", prompt("alpha.root", "[left](0a.md) [right](0b.md)"))
            graph = m.load_graph(root)
            self.assertFalse(graph.errors, graph.errors)
            self.assertEqual(len(m.reference_reach(graph)), 37)

    def test_check_bounds_witnesses_without_changing_totals_or_order(self):
        with workspace() as root:
            f = Fixture(root)
            links = []
            for number in range(25):
                path = f"src/cases/{number:02d}.md"
                f.put(path, prompt(f"case.{number}", "A reference witness."))
                links.append(f"[case](cases/{number:02d}.md)")
            f.put("src/entry.md", prompt("alpha.root", "\n".join(reversed(links))))
            graph = m.load_graph(root)
            bounded = m.check_lines(graph)
            complete = m.check_lines(graph, limit=None)
            bounded_reach = [row for row in bounded if row.startswith("REACH\t")]
            complete_reach = [row for row in complete if row.startswith("REACH\t")]
            self.assertEqual(len(bounded_reach), 20)
            self.assertEqual(len(complete_reach), 26)
            self.assertEqual(bounded_reach, complete_reach[:20])
            self.assertIn("ROOT_REFERENCE_REACH\t26", bounded)
            self.assertIn("REACH_SHOWN\t20", bounded)
            self.assertIn("REACH_SHOWN\t26", complete)
            self.assertTrue(any("omitted" in row and "--all" in row for row in bounded))
            self.assertFalse(any("omitted" in row for row in complete))

    def test_check_cli_limit_and_all_do_not_hide_late_validation_errors(self):
        with workspace() as root:
            f = Fixture(root)
            base = [sys.executable, str(ROOT / "tools/modules.py"), "check", "--root", str(root)]
            limited = subprocess.run(base + ["--limit", "1"], capture_output=True, text=True)
            full = subprocess.run(base + ["--all"], capture_output=True, text=True)
            self.assertEqual(limited.returncode, 0, limited.stderr)
            self.assertEqual(full.returncode, 0, full.stderr)
            self.assertIn("REACH_SHOWN\t1", limited.stdout)
            self.assertIn("REACH_SHOWN\t2", full.stdout)
            # An unlinked file is outside the displayed reach, but still validated.
            f.put("src/zzz.md", prompt("case.unlinked", "[broken](missing.md)"))
            for flags in (["--limit", "1"], ["--all"]):
                failed = subprocess.run(base + flags, capture_output=True, text=True)
                self.assertEqual(failed.returncode, 1, failed.stderr)
                self.assertIn("missing.md", failed.stdout)
                self.assertEqual(failed.stdout.splitlines()[-1], "FAIL")
                self.assertNotIn("PASS", failed.stdout)

    def test_check_cli_preserves_structure_failure_with_passing_size_gate(self):
        with workspace() as root:
            f = Fixture(root)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            f.put("memory-limits.json", json.dumps({"default": 200, "indexed_src": True,
                "include": ["AGENTS.md"], "rules": [], "files": {}}))
            base = [sys.executable, str(ROOT / "tools/modules.py"), "check", "--root", str(root)]
            passing = subprocess.run(base, capture_output=True, text=True)
            self.assertEqual(passing.returncode, 0, passing.stdout + passing.stderr)
            self.assertIn("SIZE_CHECK\tPASS", passing.stdout)
            f.put("src/nested/leaf.md", prompt("beta.leaf", "[broken](missing.md)"))
            failed = subprocess.run(base, capture_output=True, text=True)
            self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
            self.assertIn("missing.md", failed.stdout)
            self.assertIn("SIZE_CHECK\tPASS", failed.stdout)
            self.assertEqual(failed.stdout.splitlines()[-1], "FAIL")
            f.put("src/nested/leaf.md", prompt("beta.leaf", "x" * 250))
            over = subprocess.run(base, capture_output=True, text=True)
            self.assertEqual(over.returncode, 1, over.stdout + over.stderr)
            self.assertIn("SIZE_OVER\tsrc/nested/leaf.md", over.stdout)
            self.assertEqual(over.stdout.splitlines()[-1], "FAIL")

    def test_cli_records_modes_reasons_and_rejects_old_decisions(self):
        with workspace() as root:
            Fixture(root)
            base = [sys.executable, str(ROOT / "tools/modules.py"), "assemble", "--root", str(root)]
            result = subprocess.run(base + ["--support", "alpha.root:nested/leaf.md|episode evidence"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("USE\talpha.root\tsupport\tsrc/nested/leaf.md\tepisode evidence", result.stdout)
            rejected = subprocess.run(base + ["--decision", "alpha.root:old=true|reason"],
                                      capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)

    def test_unfinished_local_execution_state_does_not_gate_repository(self):
        with workspace() as root:
            f = Fixture(root)
            local = f.put(".mnemorph-local/learning.md", "[unfinished candidate](not-ready.md)")
            graph = m.load_graph(root)
            self.assertFalse(graph.errors, graph.errors)
            self.assertNotIn(local, graph.contents)

    def test_live_repository_check_and_cheap_root(self):
        graph = m.load_graph(ROOT)
        self.assertFalse(graph.errors, graph.errors)
        active = m.load_task_graph(ROOT)
        assembly = m.assemble(active)
        self.assertPass(assembly)
        self.assertEqual({module.module_id for module in assembly.modules},
                         {"core.entry"})
        for module_id in ("core.learn.loop", "core.memory.reflect"):
            self.assertNotIn(graph.by_id[module_id].path, active.contents)

    def test_fresh_copies_have_identical_check_output(self):
        outputs = []
        for _ in range(2):
            with workspace() as root:
                shutil.copytree(ROOT / "src", root / "src")
                shutil.copytree(ROOT / "integrations", root / "integrations")
                shutil.copytree(ROOT / "memory-template", root / "memory-template")
                shutil.copytree(ROOT / "tools", root / "tools", ignore=shutil.ignore_patterns("__pycache__"))
                for name in ("AGENTS.md", "README.md", "CONTRIBUTING.md", "LICENSE", "memory-limits.json"):
                    shutil.copy2(ROOT / name, root / name)
                subprocess.run(["git", "init", "-q", str(root)], check=True)
                result = subprocess.run([sys.executable, str(root / "tools/modules.py"), "check"],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                outputs.append(result.stdout)
        self.assertEqual(*outputs)


if __name__ == "__main__":
    unittest.main()
