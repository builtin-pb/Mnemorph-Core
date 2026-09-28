---
id: core.learn.models
summary: Which model and effort does which work — judgment to the strongest available model, bounded checkable subtasks to cheaper ones, a stronger final check when a cheaper host owns open-ended work — with what agents can and cannot choose.
---

# Model routing

Choose the model and reasoning effort for each role by the judgment it needs and the cost of an error, within the user's budget. Record host, model and effort with results; they shape behaviour.

## Roles

- **Judgment.** Deciding what matters, how deep to go and when work is done; synthesis the user will rely on, such as personal memory, reflection or a change to a method or prompt; the Taste Guard; the final check of substantive work. Use the strongest available model at high effort.
- **Bounded, checkable subtasks.** Work whose result the owner verifies: reading an assigned part of a source and returning dated facts with locations, searches, running scripts or tests, mechanical edits, extraction into a checkable format. Use a cheaper model.
- **Final check on a cheaper host.** When a cheaper model owns open-ended work, have a stronger model compare the result with the request before it is reported done.
- **Harder host.** Tests of whether a method carries a weaker model use one deliberately and say so.

When a model misses judgment its instructions already require, route that judgment to a stronger model, or give it an operation with a checkable intermediate, rather than repeating the instruction. In one replayed import, a written reading order made a cheaper model read a whole long source, yet only per-chunk fact notes made it record the events that mattered.

Vendors release models every few weeks; recheck which available model and price fit each role when a new model appears.

## Observed habits

Models differ in habit as well as strength. One may keep to a request's literal scope and leave facts it has just read unused in a draft for someone else; another may read widely but fill gaps with inference stated as fact or relay unchecked claims. A literal-scope model may carry out concrete steps in its instructions almost every time while no stated principle changes its route; for it, express the judgment it needs as a step with a visible output, as the Codex integration's [carry-through steps](../carry-through.md) do. Plan checks around the habit of the model doing the work, and test a lesson on the model that showed the miss ([lesson tests](../memory/lesson-tests.md)). An instance's settings can record which of its models showed which habit.

## What agents can choose

Agents choose models only for work they launch: subagents, Guard delegates, `codex exec` runs and experiments. The main session, scheduled tasks and Codex automations take model and effort from app settings that only the user controls; a scheduled reflection, if configured, runs on the host's default, which may be weaker or lower in effort than its judgment needs. Report such a mismatch with these roles to the user rather than working around it. Whether Codex subagents can run a different model is unchecked.
