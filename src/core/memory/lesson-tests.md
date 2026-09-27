---
id: core.memory.lesson-tests
summary: Test whether a changed instruction carries its lesson by replaying the task that prompted it, checking concrete lessons mechanically and judgment lessons blind against the correction, and recording the result.
---

# Lesson tests

A changed instruction is a candidate until a replay shows it changes behavior. Writing a rule does not show it is learned: in every repeat of a recorded correction traced in one instance, the correcting words were already in the erring agent's context. Spend replays where they matter most: the user's repeated or emphasized corrections first.

## Replay

Methods adopted before this step have no replays. A periodic run also tests one such method each run, most-used first, starting with any `primary` method, by replaying a past task it governed.

Take the task that prompted the lesson, or a close variant when the original cannot be rerun. Replay on the model that missed it, which transcripts record, since models differ in habit ([models](../learn/models.md#observed-habits)). Have a fresh agent find the cases in the record and the hosts' transcripts: the request before each correction the lesson answers, labelled repeats first, and for the no-harm check nearby requests where it should not fire, including corrections of over-applying. Keep them, with host, model, line and the commit before each, under `.mnemorph-local/cases/` to reuse. Then check it does no harm, on the model that missed it and each other mainstream model the instance lists in `src/lesson-models.json`: run about ten real past requests where the lesson should not apply, with the memory before and after the change, and compare false alarms, length and commands. A lesson rarely harms its own case; it harms other tasks by over-applying, and one fixing a model's habit cannot gain where the habit never was. `tools/codex_replay.py` runs one Codex replay from a throwaway copy with connectors and browsers off. A lesson is learned when it passes on the model that missed it and its no-harm check passes on every listed model; a Core change offered upstream is also compared across all of them, with and without Core, before release; a model a run cannot reach, such as Codex from an unattended Claude run without permission to start Codex, leaves it a candidate. Give a fresh subagent that task with the memory as changed, without mentioning the lesson, and tell it not to write memory. Give another fresh subagent the same task with the memory before the change, for example a worktree at the parent commit. Both runs load Mnemorph from their tree and take no external actions. When the original task was a long session, replay it from the transcript up to the turn that went wrong. Keep outputs under `.mnemorph-local/lesson-tests/`.

## Judge

- **Concrete lesson:** something a script or search can check, such as a word, format, count, section or placement. Check both outputs mechanically. The lesson holds when the new output passes and the old one fails. If both pass, the replay did not reproduce the miss: record `unverified`, not `pass`.
- **Judgment lesson:** it needs reading, such as how an ending lands, whether reasoning connects or whether a claim is scoped. Give a fresh judge the correction in the user's words with its reason and the two outputs, unlabeled and in random order, and ask which one the correction fits better and why. First validate the judge: give it the output the user corrected beside a repaired one, and revise its brief, without naming the answer, until it picks against the corrected output.
- Ask the person who gave the correction to judge only when a judge cannot decide or its reason looks unsound, since it costs their attention. An unattended run records `unverified` and lists it for them.

If the miss recurs, the instruction has not carried the lesson. Make it more concrete or checkable, or move it into an operation, and replay once more. If it still fails, take it out of active memory, which keeps memory lean, and park it: record `parked` with the removed text and the commit that removed it (`--removed-text`, `--parked-by`), beside the correction and its cases. Later Learn work picks parked lessons up (`python3 tools/lessons.py list --result parked`), first when their correction repeats.

## Record

Record each replay with `python3 tools/lessons.py record`: the lesson, the changed file and commit, the case, the kind, the check or judge brief, the result (`pass`, `fail` or `unverified`), the evidence path, and the failing and replay host/model (`--failed-on`, `--replayed-on`), one record per model. `python3 tools/lessons.py candidates <range>` lists, per guidance file changed in a commit range, the changes not yet learned, naming the models still missing or not passed; report them as candidates, not as learned.
