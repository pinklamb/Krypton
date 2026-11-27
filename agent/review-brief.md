# Krypton review brief (for your coding agent)

Krypton 2.1 provides the commands used below (`scan --log`, `review --json`, `restore`).

Use this when a ticket's refactoring runs are finished and some functions are still too tangled. Your job is to let the human look at the
result and decide, before anything goes to GitHub.

## Steps

0. Log a run before you start (`krypton scan . --log --ticket "what you are fixing"`: run 0) and after each change. Run the commands from the top folder of the git repository.
1. Run `krypton review --json`. It prints the ticket, the purity score now, and for each function that did not improve by 10% in three runs: its
   score at the start and after each run, and which run was best. A function's score is "times the limit" (1× is the most it may have): lower is better.
   `target` and `meets_target` say whether the ticket reached the score the person set; keep working toward it before you ask for review.
   Look at `mode` first. It is what the person chose in `krypton setup`:
   - `"auto"`: Krypton has already put back the best run (`auto_approved` says which). Build no page; tell the person in one line, then continue to the GitHub steps.
   - `"manual"`: build the page below for **this ticket only** (the one in the review data), and wait for the person.
2. Read `themes.md`. It only changes how the page **looks** (colors, fonts, shape, wording). It never changes what the page contains.
   Anything it leaves blank is your choice.
3. Build **one self-contained HTML page** on this machine (no network, no external files) with the outline below.
4. Add a small local script (about 20 lines) that listens on `127.0.0.1` on a random port, accepts one decision per stuck function from the page
   (approve or send back) and settings saves (see below), with a one-time token in the address, writes `.krypton/decisions.json`, and exits when finished.
5. Open the page and tell the human where it is. Wait for the decisions.
6. If the human approved anything: run `krypton restore --run N` once, with the `restore_run` from the review data (the run with the best purity; it puts back the whole working tree as it was at that run, not one function), then continue to the GitHub steps (push, pull request).
   For each function **sent back**: try again on that function only, log the new run, and show the page again.

## The outline (always the same, whatever themes.md says)

- **Header:** the ticket name, **purity now**, and **purity if you approve**, with the difference (▲ +3, ▼ −2 or ● no change).
  Take "if you approve" from `purity.if_approved` in the review data: the purity at `restore_run`, which is what approving puts back.
- **One card per stuck function:**
  - the file and function name and a one-line plain-English reason it is still tangled
  - the best score reached, said as "N× the safe limit" (1× is the most a function may have, so 43× is far over it), in plain words a non-technical
    reader understands, for example "Still very hard to read, even at its best (run 2)"; also the latest score if it differs from the best
  - a row of small chips with the score at the start and at each run, the best one marked (no graph)
  - one sentence: what approving adds to purity (or "No run beat the start, so approving keeps the original")
  - two buttons: **Approve best** and **Send back**. Words on the buttons come from `themes.md` if it names them.
- **Footer:** how many functions improved, how many are stuck, purity now and after, and a **Finish** button that is enabled once every function is decided.
- If nothing is stuck: show the header and "All clear", and a Finish button.

## The settings panel (on the same page)

The page also carries a small **Settings** panel, styled by `themes.md` like the rest, so the person can change how Krypton runs without the
terminal. Read the current values from `mode`, `target` and `max_runs` in the review data.

- **Target score** (0-90; no code scores above 90): the purity each ticket's code must reach. If `.krypton/settings.json` has a `baseline`, show it beside the target
  ("your codebase scores 28 today") so the person can aim above it.
- **Max tries** (1-10): runs on a function before it counts as stuck.
- **When stuck:** Ask me / Auto.
- A **Save** button. The local script saves by running `krypton setup --no-map --mode M --target T --runs N` (never by writing the file
  itself), shows what the command printed, and the page reloads its numbers. If the command fails, show its message unchanged.

## Rules

- Escape every file and function name before putting it in the page: they come from the code and are not trusted.
- Never send anything over the network. The page and the script run only on this machine.
- Do not change the numbers. Show exactly what `krypton review --json` returned.
- Do not push or open a pull request until the human has decided every stuck function.
