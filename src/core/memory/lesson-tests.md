---
id: core.memory.lesson-tests
summary: Test a changed instruction by cost, replaying it when cheap and otherwise removing misleading text untested or red-teaming it for real runs to judge; check concrete lessons mechanically and judgment lessons blind, and record the result.
---

# Lesson tests

A changed instruction is a candidate until a replay or real runs show it changes behavior. Writing a rule does not show it is learned: in every repeat of a recorded correction traced in one instance, the correcting words were already in the erring agent's context.

## Choose the test by cost

Real runs are the standard: later sessions exercise every instruction they load. Replay before adopting when cheap or important; otherwise check the text and let real runs decide.

- **Cheap, or a correction the user repeated or emphasized: replay it now**, as below.
  - Cheap means a few runs can show the effect, as for a concrete step or a rule shown at the decision, forked there.
  - A reworded principle rarely is: its effect is usually too small for a few runs to show.
- **Expensive, and the edit only removes text that invites a wrong reading: remove it untested.**
  - Quote the wrong reading, from a correction, a log or a rule it contradicts. "Shorter" is not one.
  - It is a removal only if the diff adds no instruction text. A rewrite or compression is an edit.
  - If it drops a limit or qualifier, first have a fresh agent list the distinctions lost. In one instance, dropped qualifiers were most of the removals later restored.
- **Expensive otherwise: red-team it, adopt it as a candidate, and let real runs judge.**
  - Give fresh reviewers the user's own requests and corrections, the failure the edit targets and the new text. Ask how an agent could still behave badly with it in context.
  - Treat findings as hypotheses; check them against the logs where those can show it. Revise or drop the edit for each that holds, preferring a removal, a concrete step or delivery at the decision to more principle text.
  - A review reads the text fresh and misses failures that appear late, on later wakes, deep in a thread or after compaction. For those, replay at that distance (below) or rely on real runs.
  - Name what real runs would show if the edit works or does harm, such as firing where it should not, and a date to check, counted from when sessions start loading it. Record these with `lessons.py record`, case `real runs until <date>`.
  - Park it (below) if real runs show no gain or some harm. If they have not settled it, leave it and list it for the user.

## Replay

A periodic run also tests one method adopted before this step, most-used first, starting with any `primary` method, by replaying a past task it governed whose correction it should have anticipated; a preference about one piece is not such a case.

Take the task that prompted the lesson, or a close variant when the original cannot be rerun. Memory soon records that case's own correction, so replaying it shows only that the correction was kept; test a general rule also on held-out cases whose specifics memory does not hold, such as later cases, public sessions or a copy stripped of the case's records. When the miss came turns or hours after the instruction it broke, replay at that distance, resuming the session just before the failing turn (`tools/replay.py codex-fork` or `claude-fork`), not as a fresh task. Replay on the model that missed it, which transcripts record, since models differ in habit ([models](../learn/models.md#observed-habits)). Have a fresh agent find the cases in the record and the hosts' transcripts: the request before each correction the lesson answers, labelled repeats first, and for the no-harm check nearby requests where it should not fire, including corrections of over-applying. Keep them (host, model, line, commit before each) under `.mnemorph-local/cases/` to reuse. Then check it does no harm, on the model that missed it and each other mainstream model the instance lists in `src/lesson-models.json`: run about ten real past requests where the lesson should not apply, with the memory before and after the change, and compare false alarms, length and commands. Run every arm through `tools/replay.py` (`codex` or `claude` with `--model`, `codex-fork` or `claude-fork` at distance, `batch` for a set), a sealed throwaway copy with connectors and browsers off, never as a subagent of your own session, which keeps your live apps and accounts. A lesson is learned when it passes on the model that missed it and its no-harm check passes on every listed model; a Core change offered upstream is also compared across all of them, with and without Core, before release; a model a run cannot reach leaves it a candidate. Compare memory as it was when the case happened, with and without only the change under test (`--commit` at the case, `--patch` for the change); today's memory, which records the case's later corrections, is not an arm. Neither arm's prompt mentions the lesson. A case whose turn sent, posted, scheduled, bought or changed a setting stays usable: give both arms [`replay_no_actions.md`](../../../tools/replay_no_actions.md), by `--global-agents` or a fork's `--append`, so the agent says what it would do instead; only for such turns, since a line beside the request changes behavior. Keep outputs under `.mnemorph-local/lesson-tests/`.

## Judge

- **Concrete lesson:** something a script or search can check, such as a word, format, count, section or placement. Check both outputs mechanically. The lesson holds when the new output passes and the old one fails. If both pass, the replay did not reproduce the miss: record `unverified`, not `pass`.
- **Judgment lesson:** it needs reading, such as how an ending lands, whether reasoning connects or whether a claim is scoped. Give a fresh judge the correction in the user's words with its reason and the two outputs, unlabeled, in random order, and ask which one the correction fits better and why. First validate the judge: give it the output the user corrected beside a repaired one, and revise its brief, without naming the answer, until it picks against the corrected output.
- **Route lesson:** the same checked result with less effort. Worth testing when the saving repays the replays: run each arm at least twice, check the result mechanically every time and compare tokens, time and calls. It holds when every run reaches the result and the arms differ beyond each one's spread; a cheaper run that misses the result is a `fail`.
- Ask the person who gave the correction to judge only when a judge cannot decide or its reason looks unsound, since it costs their attention. An unattended run records `unverified` and lists it for them.

If the miss recurs, the instruction has not carried the lesson. Make it more concrete or checkable, or move it into an operation; replay again. One run shows only large effects: a failure counts only where the correction's own words beside the request avoided the miss, on several cases with two runs each; otherwise record `unverified` and keep it unless its context cost outweighs it. Park a lesson that fails such a test, removing it from active memory to keep it lean: record `parked` with the removed text and the commit that removed it (`--removed-text`, `--parked-by`), beside the correction and its cases. Later Learn work picks parked lessons up (`python3 tools/lessons.py list --result parked`), first when their correction repeats.

## Record

Record each replay with `python3 tools/lessons.py record`: the lesson, the changed file and commit, the case, the kind, the check or judge brief, the result, the evidence path, and the failing and replay host/model (`--failed-on`, `--replayed-on`), one record per model. `python3 tools/lessons.py candidates <range>` lists, per guidance file changed in a commit range, the changes not yet learned, naming the models still missing or not passed; report them as candidates, not as learned.
