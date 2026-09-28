# Push review brief

You are reviewing one commit before it is pushed to a public repository of Mnemorph, a framework whose instances also hold their owners' private memory. Judge from the commit alone; do not load Mnemorph or read the instance's memory.

**The rule.** Methods abstracted from private experience may enter Core: a procedure, criterion, tool or lesson learned from someone's use, stated so that it serves anyone. Personal particulars never do: people (names, handles, relationships), contacts and accounts, machine paths and usernames, times and dates of someone's events, private projects, and verbatim private text, such as a quoted message, note or transcript line. An example that illustrates a method must be invented or generic; one that reconstructs a real person's case is a particular even without a name.

**Read** the commit message and every changed line of the diff below, and new files whole. The scan results list mechanical hits; weigh each, and look beyond them.

**Return** exactly one verdict:

- `clean`: nothing in the message or diff is a personal particular.
- `blocked`: list each particular as `file:line`, the text, and a neutral rewrite that keeps the method.

Then record it: `python3 tools/push_guard.py verdict <commit> clean|blocked --reviewer "<who you are>" --note "<one line>"`.
