# Mnemorph

**Memory that learns how you work.**

Coding agents remember what you told them. Mnemorph learns from it. Every correction you give Claude Code or Codex becomes a lasting change to how they judge, plan and work, and it carries to every case where the same reason holds. Your memory is a Git repository of plain Markdown that both agents load on every task.

## What it does

- **Learns the reason, not just the rule.** Mnemorph reads each correction for why it holds and when, then revises the method that went wrong.
- **Tests what it learns.** A new rule stays a candidate until replaying the task that taught it shows the new behavior, and replaying unrelated past requests shows it breaks nothing.
- **Thinks before it acts.** Every task starts by checking what it remembers against your request: is it already done, does it rest on a wrong premise, is there a better route? It settles the details itself and asks you only about goals and costly choices.
- **Stays yours.** Every change is a commit you can read and revert, and one memory serves both Claude Code and Codex.

## Get started

You need Git, Python 3.10+, and Claude Code or Codex.

~~~sh
git clone https://github.com/builtin-pb/Mnemorph-Core.git my-mnemorph && cd my-mnemorph
git remote rename origin upstream
cp -R memory-template/. src/
git add src && git commit -m "Start memory"
python3 tools/push_guard.py install origin
~~~

Then:

1. Link your copy into [Claude Code or Codex](integrations/README.md).
2. To back up your memory, add a **private** repository as `origin` and push to it.
3. Open a session and run `/personalize`.
4. Schedule [nightly reflection](integrations/README.md#nightly-reflection), so Mnemorph learns from each day's work.

## Use it

Mnemorph loads on every task without being asked. Work as usual, and when your agent gets something wrong, just say so: Mnemorph notes your words, and reflection files them into memory. Four commands do more:

| Command | When to use it |
| --- | --- |
| `/personalize` | First, and whenever you have new material. It reads what you offer, such as agent histories, documents, and chat or mail exports, and writes an account of your life and work, so you stop repeating yourself. |
| `/reflect` | After a session worth learning from; the nightly run does the same for each day. It files what you said where it belongs and revises or forgets what has gone stale. |
| `/learn` | To build or improve a prompt, a workflow or a capability, Mnemorph's own included. |
| `/taste` | On work that matters. It starts a Taste Guard, an independent agent that asks *whether* and *why* one level above the work, up to whether the whole project should change course. |

In Codex, type `$` instead of `/`, as in `$personalize`.

## Core and your instance

This repository is **Mnemorph-Core**: shared methods, tools and host integrations, and no one's memory. The methods start at [the Core entry](src/core/__entry__.md). Your clone becomes your **instance**: Core plus your own memory in `src/`. You can change anything in it, Core included, and take official updates when you want them:

~~~sh
git fetch upstream
git log --oneline HEAD..upstream/main   # see what's new
git merge upstream/main                 # or cherry-pick only what you want
~~~

## Privacy

Your memory lives in your instance. Push it only to a private repository. The push guard refuses to send anything outside Core's files to any remote you have not marked private. Keep secrets and credentials out of memory.

## Contributing

Improvements to Core are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) explains how to offer them from your instance.

## License

[Apache-2.0](LICENSE)
