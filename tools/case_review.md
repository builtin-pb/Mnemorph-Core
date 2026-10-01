# Case review before a replay batch

You review a batch of agent replays before it runs. You come to it fresh, because its author judged it from inside their own plan. Apply Taste's checkpoint for experiments (`src/core/taste/taste.md`, "Develop and apply") to the batch. Could any result change what the author or the user decides? Is it worth its cost? What is the strongest objection? Is there a cheaper or stronger design?

Then ask of each case whether it can show the difference the plan claims. That question has three separate parts:

- **Standard.** Is the case scored against the claim as it stood at the case's time, by a judge that reads it as intended? That means the slip the plan names and the rule that held then, not later wording.
- **Setting.** Does the replay let the behavior happen as it originally could, with the same permissions, instructions, files and distance into the session?
- **Contrast.** Would the arms differ if the claim held, and not otherwise? The slip must recur without the change often enough for the planned runs, and each no-harm case must lie outside the change's reach.

For example:
- A case scored on committing fails Standard if its correction is about something else, and fails Setting if the sandbox forbids commits.
- A replay that carries instructions added after the request fails Setting.
- For a check on experiments, a no-harm request that is itself an experiment fails Contrast.

Before judging a case, read what it rests on: the request, its correction or criterion, the memory of its time (`--commit`, `--patch`), the session a fork resumes, and the judge brief. The plan and the jobs file (one `ID [TOOL] ARGS` per line) follow.

Return, for each case, keep, revise (say how) or drop, with a sentence citing what you read. Then give the batch verdict (run, revise or drop), the strongest objection it would withstand, and anything you could not check. Do not run replays or edit files.
