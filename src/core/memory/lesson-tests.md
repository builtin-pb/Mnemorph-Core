---
id: core.memory.lesson-tests
summary: Test whether a changed instruction carries its lesson by replaying the task that prompted it, checking concrete lessons mechanically and judgment lessons blind against the correction, and recording the result.
---

# Lesson tests

A changed instruction is a candidate until a replay shows it changes behavior. Writing a rule does not show it is learned: in every repeat of a recorded correction traced in one instance, the correcting words were already in the erring agent's context. Spend replays where they matter most: the user's repeated or emphasized corrections first.

## Replay

Methods adopted before this step have no replays. A periodic run also tests one such method each run, most-used first, starting with any `primary` method, by replaying a past task it governed.

Take the task that prompted the lesson, or a close variant when the original cannot be rerun. Replay on each mainstream model the instance lists in `src/lesson-models.json`, and on the model that missed it, since models differ in habit ([models](../learn/models.md#observed-habits)); without a list, on the model that missed it. `tools/codex_replay.py` runs one Codex replay from a throwaway copy with connectors and browsers off. A lesson is learned only when it passes on every one of them; a model a run cannot reach, such as Codex from an unattended Claude run without permission to start Codex, leaves it a candidate. Give a fresh subagent that task with the memory as changed, without mentioning the lesson, and tell it not to write memory. Give another fresh subagent the same task with the memory before the change, for example a worktree at the parent commit. Both runs load Mnemorph from their tree and take no external actions. When the original task was a long session, replay it from the transcript up to the turn that went wrong. Keep outputs under `.mnemorph-local/lesson-tests/`.

## Judge

- **Concrete lesson:** something a script or search can check, such as a word, format, count, section or placement. Check both outputs mechanically. The lesson holds when the new output passes and the old one fails. If both pass, the replay did not reproduce the miss: record `unverified`, not `pass`.
- **Judgment lesson:** it needs reading, such as how an ending lands, whether reasoning connects or whether a claim is scoped. Give a fresh judge the correction in the user's words with its reason and the two outputs, unlabeled and in random order, and ask which one the correction fits better and why. First validate the judge: give it the output the user corrected beside a repaired one, and revise its brief, without naming the answer, until it picks against the corrected output.
- Ask the person who gave the correction to judge only when a judge cannot decide or its reason looks unsound, since it costs their attention. An unattended run records `unverified` and lists it for them.

If the miss recurs, the instruction has not carried the lesson. Make it more concrete or checkable, or move it into an operation, and replay once more; otherwise record `fail` and report the instruction as a candidate.

## Record

Record each replay with `python3 tools/lessons.py record`: the lesson, the changed file and commit, the case, the kind, the check or judge brief, the result (`pass`, `fail` or `unverified`), the evidence path, and the failing and replay host/model (`--failed-on`, `--replayed-on`), one record per model. `python3 tools/lessons.py candidates <range>` lists, per guidance file changed in a commit range, the changes not yet passed on every required model, naming the models missing; report them as candidates, not as learned.
