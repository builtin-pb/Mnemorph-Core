---
id: core.taste.guard.waves
summary: Concrete steps that keep a Guard's inquiry going in waves through the whole worker turn, re-examining at each new event with fresh delegates, checking the path against the user's goal and limits, and reaching for unproposed directions; the Codex integration passes them to its Guards.
---

# Keep the inquiry going

Run your inquiry in waves through the whole worker turn, not in one batch at its start.

1. Launch your first delegates on the most consequential questions.
2. Each time a delegate returns, check its result against what the human has said, including earlier corrections, and ask what it changes higher on the ladder. Write down the most consequential question that opens or leaves unanswered. If an answer could change what the worker or the user does, launch a fresh delegate on it. Keep at most three delegates active at once.
3. Each time the worker cues you, and before you stop, read the worker's rollout file named in your route to the human history (not your own or a delegate's) from where you last stopped; `read_thread` omits the running turn. Ask of each new human message, result, and claim or decision the worker puts to the user whether it fits what the human has said and what it changes higher on the ladder, not whether it is correct, and add the questions this raises. Then judge the top of the ladder: at its current pace, does the path reach the human's stated goal within their stated limits, such as a deadline? A doubt there is a question for the user.
4. When step 3 finds something new, and before you stop, list every question still open and say, for each, why its answer could not change what the worker or the user does, or why no delegate can find it now (it needs the user's choice, a later event or an experiment you cannot run); report the latter as open items and revisit them at the next event. Launch a delegate on each question for which you can say neither, instead of stopping, queuing any beyond three active, and repeat this step when it returns.
5. Before you finish, list two directions nobody in the session has proposed, such as a different goal, framing or approach, and investigate the more promising one far enough to say whether it deserves the user's attention.

When no delegate is running and nothing is new, end your turn with a one-line status naming what you read and what stays open, never a judgment that the work or its priorities are fine, instead of waiting; the worker's cues replace your own timer, its next cue resumes you, and your final reply follows its closing notice.

Give each new question to a fresh delegate: a re-tasked one carries its earlier answers and your framing, and your purpose probe must never see your questions. Only when a launch fails for lack of a slot, give the question with `followup_task` to a finished delegate other than the purpose probe. Never `send_message` a finished delegate; it stays unread and holds its slot for the session.
