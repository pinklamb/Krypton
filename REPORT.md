# Retro Grid Highway Racer: Version A vs Version B (Argon-gated)

Both games are playable by opening `version_a/index.html` or `version_b/index.html` (no build step, no server).
Controls: `←/→` or `A/D` change lane, hold `↑` or `W` for Nitro, `Space`/`Enter` restarts after Game Over.

**How this was measured.** Every number below comes from a script in `tools/` run against frozen copies of each stage in `snapshots/`
(`a_step1`, `a_step2`, `a_final`, `b_step1`, `b_step2`, `b_final`):

| Script | What it produces |
|---|---|
| `tools/count_tokens.py` | exact `cl100k_base` counts (tiktoken) over every `.html/.css/.js` file |
| `tools/argon_stats.py` | Argon 0.2.0 JSON for each snapshot |
| `tools/cache_model.py` | prompt-cache cost model, driven by real token-level prefix overlap between snapshots |
| `tools/smoke.js` | headless behavioural test (stub canvas) run against all 6 snapshots; **all pass** |

**Not verified:** I did not open either game in a real browser. The smoke test drives real frames through the real game code with a stub canvas, so it checks game logic (lanes, speed, score, nitro 2×, exhaust, 3 lives, 5 shards, 2 s shake, padding, game over, restart). It does **not** check how anything looks (glow, colours, shake feel).

---

## 1. End-project code architecture review

**Verdict: Version B is the cleaner and easier-to-extend architecture by the end of Feature 2, but not by a free margin.** It is 56% larger in tokens, spread over 18 files, and still has two coupling hot spots.

| Metric (final) | Version A | Version B |
|---|---|---|
| Files (html/css/js) | 3 | 18 |
| Functions rated by Argon | 19 | 79 |
| Argon purity score | **21/100** (fails; exit 1) | **100/100** (exit 0) |
| Flagged files | 1 of 1 (`game.js`, rating 4840 vs threshold 648) | 0 of 16 |
| Worst function | `update()`: 11 branches, 40 effective lines, rating 4840 | `renderScene()`: 3 branches, rating 144 |
| Deepest nesting | 2 | 1 |
| Argon score trajectory (step 1 → nitro → final) | 25 → 22 → 21 | 100 → 100 → 100 |

**Locality of each feature's edits** (from `git diff --no-index` between snapshots):

| Update | Version A | Version B |
|---|---|---|
| Nitro | 2 files, +67/−3, 12 hunks, 0 new files | 8 files, +105/−7, 17 hunks, 2 new files |
| Lives/Shake/Shards | 1 file, +111/−10, **13 hunks in one file** | 9 files, +194/−11, 18 hunks, 4 new files |

**Why B comes out ahead**
- Each new mechanic became its own module (`nitro`, `exhaust`, `collision`, `health`, `shake`, `shards`) with small, individually testable methods. The collision rewrite replaced one method and deleted `obstacles.hitsPlayer`; nothing else needed to know.
- A's `update()` absorbed all three features: time scaling, score multiplier, particles, effects timers, invulnerability and hit handling. It went from 6 to 11 branches and is the only function Argon rates as a problem. Every future feature has to be threaded through it, and through `render()` (2 → 7 branches).
- In A, Feature 2 needed 13 separate insertions into one 330-line file, several of them order-sensitive (see §4).

**What B did not fix**
- `world.js` (integration hub), `config.js` and `index.html` were touched by **every** feature. Isolation moved the new logic out, but the wiring still funnels through three hot files. `world.js` is the largest file at 762 tokens.
- Everything hangs off a global `Racer` namespace with a hand-maintained `<script>` order. A wrong order is a runtime error that Argon cannot see.
- Some of B's granularity was shaped by the metric. `input.handleRestartKey` exists mainly to keep `handleKeyDown` at 2 branches. Argon's own README warns against splitting into trivial wrappers; this is a mild instance.
- B is not "better because Argon says 100". Argon also failed A from the first commit even though A's game behaves identically in the smoke test. Argon measures structural weight, not correctness (see the bug in §4 that it did not catch).

**When A would be the better choice:** a throwaway prototype. It is 1,730 tokens at the start, one file, no load-order concerns.

---

## 2. Chronological source token counts (`cl100k_base`, exact)

| Stage | Version A | Version B | B ÷ A |
|---|---:|---:|---:|
| Initial build (Step 1) | **1,730** (3 files) | **2,879** (12 files) | 1.66× |
| After Nitro (Feature 1) | **2,302** (3 files) | **3,732** (14 files) | 1.62× |
| After Advanced Collisions (Feature 2, final) | **3,272** (3 files) | **5,120** (18 files) | 1.56× |
| Growth, Step 1 → final | +1,542 (+89%) | +2,241 (+78%) | |
| Growth per feature | +572, +970 | +853, +1,388 | |

Counts cover every `.html`, `.css` and `.js` file in the snapshot, using the raw file text. `AGENTS.md` (Argon rules for agents) and the test tooling are excluded. B's Step 1 figure is the post-Argon-feedback version (see §4); I did not keep the first draft that scored 78/100.

Largest files at final: A `game.js` 3,034 tokens (93% of the project); B `world.js` 762, `hud.js` 622, `road.js` 498.
B pays a fixed structural premium (namespace headers, `this.`/`Racer.` prefixes, `create/step/draw` triads) but grows more slowly in relative terms, since A grows inside one file.

---

## 3. Conversational prompt-cache cost matrix (30 turns)

**Pricing (from the brief):** $3.00 per M uncached input tokens, $0.30 per M cache-hit tokens.

**Model.** This is an estimate, not a bill.
- 30 turns = 3 phases × 10 turns (build, Nitro, Lives/Shake).
- Every turn re-sends the whole source tree as context.
- A cache hit covers only the longest token-identical prefix shared with the previously cached prompt. Everything after the first changed token is billed uncached.
- The prefix overlap between consecutive snapshots was **measured**, not guessed, for three file orderings: *natural* (`index.html`, `style.css`, scripts in load order), *creation* (`index.html` last) and *volatility* (hindsight: least-edited files first).
- Output tokens and the system/chat overhead (identical for both versions) are excluded.

Scenarios:
- **S1, batched edits:** the code changes once at the start of each phase, and the other 9 turns re-read it fully cached.
- **S2, worst case:** the code is edited on every turn at that phase's first-divergence point.

| Ordering | Measured reusable prefix at Nitro / Final (tokens) | | S1 batched | S2 edit-every-turn | Share of S2 prompt tokens cache-hit |
|---|---|---|---:|---:|---:|
| Natural | A: 82 / 328 | B: 82 / 173 | A **$0.0407** · B **$0.0676** | A **$0.2091** · B **$0.3523** | A 5.6% · B 2.1% |
| Creation | A: 273 / 214 | B: 246 / 285 | A **$0.0405** · B **$0.0668** | A **$0.2071** · B **$0.3449** | A 6.6% · B 4.4% |
| Volatility (best case) | A: 214 / 328 | B: 1,142 / 1,624 | A **$0.0404** · B **$0.0608** | A **$0.2056** · B **$0.2845** | A 7.4% · B 23.1% |
| *No caching at all* | | | | A $0.2202 · B $0.3592 | |

Prompt size per turn (with file headers): A 1,742 → 2,314 → 3,284 tokens; B 2,943 → 3,809 → 5,221 tokens.
If cache *writes* are priced at $3.75/M (Claude 3.5 Sonnet's actual list price, not in the brief), S1 rises to A $0.0459 / B $0.0764 (natural order).

**What this says**
1. **Version A's choices:** with everything in one `game.js`, each edit lands near the top (constants, `state` object) and invalidates almost the whole file. Only about 6–7% of the prompt tokens can be cache hits at any ordering. A's single file means ordering cannot help.
2. **Version B's layout isolation does not help by itself.** With natural or creation ordering, B reuses only 2–4% of its prompt, because `config.js` and `index.html` are edited by every feature and sit early. B only reaches 23% when the files are ordered by volatility, which means the tool assembling the prompt has to put stable files first. That ordering *is* enabled by B's file granularity but is not caused by Argon compliance.
3. **B is still more expensive in absolute terms** in every scenario (+38% to +66%), because it is 56–66% more tokens. Best-case B S2 ($0.2845) is still above A's worst ($0.2091).
4. **The dominant saving is from repeating unchanged turns (S1 vs S2: about 5×), identical for both versions.** Layout effects are second-order, at most about 10% of S1 and 19% of S2 for B (natural → volatility ordering).
5. **Caveat:** in a real agent session such as Claude Code, the conversation is append-only. File edits arrive as new messages and do not rewrite the earlier prefix, so edits would not invalidate the cache at all. This matrix models the re-send-everything workflow the brief asked for. Totals are fractions of a cent to tens of cents either way; at this project size, cost is not a deciding factor.

---

## 4. Structural integrity audit

Everything here actually happened in this session.

**Version A (editing one large file by anchored patches)**
- **A real scoping bug I introduced and Argon did not catch.** Adding Feature 2, I inserted `const MAX_LIVES = 3` *below* the `state` object that uses it (`lives: MAX_LIVES`). That is a temporal-dead-zone `ReferenceError` at page load: the game would not start. `node --check` passed and Argon reports nothing for it; the headless smoke test caught it. Fixed by moving the constants block above `state`.
- **Splice-by-search edit.** I replaced the old hit-test by cutting from one anchor (`for (const o of state.obstacles) { const hitX…`) to the next function's name (`function drawRoad`), then rebuilding the closing `}` of `update()` by hand. It worked, but closing-bracket alignment depended on my reconstruction.
- **Latent early-return trap.** `if (state.invulnTimer > 0) return;` sits mid-body of `update()`. It is currently the last statement, so it is harmless; anything appended after it later would silently stop running during the 2 s invulnerability window.
- **Mixed time bases.** Nitro forced me to rename the parameter to `realDt` and derive a scaled `dt` inside `update()`. Particles, shake and invulnerability use `realDt`; speed, score, obstacles and spawning use `dt`. Both names are in scope in one function, which makes a future wrong-variable mistake easy.
- **Accumulated state.** `resetGame()` must list every new field (`particles`, `lives`, `shakeTimer`, `invulnTimer`, `shards`). I extended it each time; forgetting one would leak state between runs.

**Version B (many small files, global namespace)**
- **Load order is a hidden contract.** `config.js` mutates `Racer.config` after creating it (`playerY`, `roadWidth`); `world.js` calls into modules that must already exist. Six new `<script>` tags had to be inserted by hand in a valid order. Argon does not see this.
- **`this` in object-literal methods.** Methods use `this.helper()`. They are safe as written (I wrapped callbacks in arrows), but `list.forEach(this.drawOne)` would silently lose `this`. A convention to remember, not something the tools enforce.
- **Hidden global read.** `Racer.nitro.isActive()` reads the `Racer.input` singleton directly, so `world.update` is not a pure function of its arguments.
- **`input.releaseAll()` reassigns `held`**, so any caller keeping a reference to the old object would go stale (none do today).
- **Argon is a real gate, not a rubber stamp.** Step 1 first draft scored **78/100**: I had written `Racer.x = function` assignments, which Argon treats as anonymous module-level code; two files were flagged. A restructure into object-literal modules (named methods) took it to 100/100. Features 1 and 2 passed at 100/100 on the first Argon run.

**Tooling noise (not code, but it cost turns)**
- Large bash heredocs silently truncated several Step 1 files. Argon flagged `hud.js` as **100% unparseable**, which is how I found it; I rewrote them with file-write tools.
- A Python patch script crashed on a non-ASCII character (`♥`) under the Windows default codec *after* opening `game.js` for writing, leaving a 0-byte file. I had a snapshot, restored it and re-ran with explicit UTF-8.

**Interpretation notes**
- "Double the game processing speed" is implemented as a 2× time scale for the world simulation, and the score multiplier is a separate 2×. Together that is **4× score per wall-clock second** under nitro (measured 4.0× in the smoke test). If you want 2× total, set `nitroScoreMultiplier` to 1 (B) or `NITRO_SCORE_MULT` to 1 (A).
- Collision "pixel overlap with safety padding" is implemented as exact pixel-extent overlap on hitboxes inset by 8 px per side; a hit needs positive overlap on both axes. After a hit the colliding obstacle is removed and the player is invulnerable for the same 2 s as the shake, with a blinking car. Without that, one crash would drain all 3 lives in a few frames.
- The JSON outputs in `tools/` (`token_counts.json`, `argon_stats.json`, `cache_model.json`) hold the raw numbers behind every table.


---
---

# Phase 5: Enterprise extension (Features 3 and 4)

**Features added to both versions:** (3) a Weather Matrix cycling Clear → Rain → Neon Fog every 30 s: rain gives 0.8× traction plus a 0.12 s lane-switch delay; fog gives a shifting colour overlay, a 0.7× spawn timer and continuous downward-drifting mist. (4) An achievement tracker with Speed Demon (5 s consecutive Nitro), Near Miss (pass within 5 px without damage) and Survivor (score 5,000), persisted unlocks (`localStorage`), a slide-down toast, and a WebAudio synth stub.

**Verification.** `tools/smoke.js` now has a Phase 4 block, run against `a_v5_final` and `b_v5_final`: weather cycle, rain delay and glide ratio (measured 1.25×), fog spawn factor (checked against the formula), mist drift, overlay drawn, each achievement (including negative cases: wide pass, collision, non-consecutive Nitro), toast expiry, no re-award, 3 oscillators per unlock, storage round-trip. Both versions pass every stage's tests. As before, **I did not look at either game in a real browser**, so rain/fog/toast visuals and the audio sound are unverified by eye or ear.

**Version B loop.** Argon was run in Docker (`docker run --rm -v "$PWD:/repo" argon version_b`) after every edit. B scored **100/100, 0 of 23 files flagged**, on the first Docker run after the features landed. Argon asked for no refactor this round; the one bug found (below) was found by the tests, not by Argon.

## 1. Enterprise growth divergence (`v5_final`)

| Stage | Version A | Version B | B ÷ A |
|---|---:|---:|---:|
| Initial build | 1,730 | 2,879 | 1.66× |
| After Nitro | 2,302 | 3,732 | 1.62× |
| After Lives/Shake | 3,272 | 5,120 | 1.56× |
| **`v5_final` (after Weather + Achievements)** | **5,323** (3 files) | **8,102** (25 files) | 1.52× |
| Growth this round | +2,051 (+63%) | +2,982 (+58%) | |
| Growth per feature, F1 → F2 → avg(F3, F4) | +572 → +970 → ~+1,026 | +853 → +1,388 → ~+1,491 | |

I did not snapshot between Feature 3 and Feature 4, so their sizes are only available as a pair.

**Did A's monolith grow exponentially? No.** Per-feature growth rose (572 → 970 → ~1,026) but about linearly: later features are simply bigger features. What did happen is concentration: `game.js` went from 1,507 to **5,085 tokens (3.4×)** and is now **96% of the project**. All 2,051 new tokens landed in that one file (+230/−5 lines, 15 hunks). Its `update()` is now 13 branches, 45 effective lines, Argon rating 7,605 against a threshold of 648, maximum nesting depth 3, and the purity score is 19/100.

**Did B stay isolated in small leaf files? Mostly, with a caveat.** Of B's +2,982 tokens, **2,252 (75.5%)** went into 7 new leaf modules (`weather`, `mist`, `atmosphere`, `storage`, `audio`, `toast`, `achievements`) and 730 into 8 existing files. 62% of that edit volume hit the same two hot files: `world.js` (+257) and `config.js` (+197). `world.js` is now 1,019 tokens, the largest file at 13% of the project. B's worst function has 4 branches (`audio.ensure`), its worst Argon rating is 180, and the max nesting depth is 1, across 124 rated functions in 23 files. In diff terms: 15 code files touched (7 new, 8 modified), 29 hunks, plus a small `AGENTS.md`.

**The uncomfortable part:** B grows faster in absolute tokens (+1,491 vs +1,026 per feature, about 45% more) and carries a 1.5× larger prompt. The isolation is real, but it does not make the repo smaller. It makes each change local and keeps each function small.

## 2. Enterprise-scale cache simulation (100,000 turns, 40,000-token repo)

**Method.** The per-turn model from §3 is scaled up. The only measured input is the **prefix-survival fraction `f`**: the share of the prompt still cache-hit after a code edit, taken token-weighted over the three edit transitions in the real snapshots (`tools/cache_model.py`, extended to 4 phases).

- Edit turn: `40,000 × (f × $0.30 + (1 − f) × $3.00) / 1M`.
- Non-edit turn: fully cached, `$0.012`.
- Pricing: $3.00/M uncached, $0.30/M hit, as in the brief.
- Output tokens and cache-write premiums are excluded.
- Bounds for 100k turns: **$1,200** (everything cached) to **$12,000** (nothing cached).

| Scenario | Measured / assumed `f` | 100k turns, edit every turn | 100k turns, 30% of turns edit |
|---|---:|---:|---:|
| **A**, measured (one `game.js`, natural order) | 7.3% (measured) | **$11,207** | **$4,202** |
| A, hypothetical "middle-of-the-block" edits (uniform position) | 50% (assumed) | $6,600 | $2,820 |
| **B**, measured, natural load order | 2.7% (measured) | $11,707 | $4,352 |
| **B**, measured, volatility-ordered | 21.0% (measured) | **$9,727** | **$3,758** |
| B, measured, append-only by stage | 21.1% (measured) | $9,721 | $3,756 |
| B, hypothetical ideal isolation | 90% (**not measured**) | $2,280 | $1,524 |

*Cumulative at 10k / 50k / 100k turns (edit every turn):* A measured $1,121 / $5,604 / $11,207; B volatility-ordered $973 / $4,864 / $9,727. Cost is **linear in turns, not compounding**, because the repo is held at 40k tokens. It compounds only if the repo (or the context) grows.

**Reading the table**
1. **Measured A vs measured B volatility-ordered:** B saves **$1,480 per 100k edit-turns (13%)**, or $444 (11%) at a 30% edit rate. That is a real but modest win, not an order-of-magnitude one.
2. **B's win depends on the prompt layout.** With natural load order B is *more* expensive than A ($11,707 vs $11,207) because `config.js` and `index.html` sit at the front and every feature edits them.
3. **The "middle-of-the-block" A is cheaper than measured B** ($6,600 vs $9,727). In this project A's edits actually landed near the top of the file (new constants and `state` fields), which is why measured A is only 7%. Where edits fall in the prompt matters more than whether the code is modular.
4. **Rule of thumb for this repo size:** every +10 points of prefix survival saves about **$1,080 per 100k edit-turns**. Reaching the "90%" row needs edits confined to a small tail of the prompt, which B's current hot files (`world.js`, `config.js`) do not give you.
5. **Extrapolation warning.** Moving from an 8k-token toy to 40k tokens assumes `f` stays the same. `f` comes from only 3 transitions per version, one run each. It is a scenario, not a forecast. In a real append-only agent session (Claude Code style) edits do not invalidate the cached prefix, and the dominant cost drivers would be the cache TTL and context growth, which this model does not capture.

The extended 4-phase (40-turn) version of the §3 table, recomputed with the same code:

| Ordering | Prefix reusable at Nitro / Final / v5 (tokens) | S1 batched | S2 edit-every-turn |
|---|---|---:|---:|
| Natural | A 82 / 328 / 393 · B 82 / 173 / 213 | A $0.0701 · B $0.1140 | A $0.3586 · B $0.5939 |
| Creation | A 273 / 214 / 279 · B 246 / 285 / 329 | A $0.0702 · B $0.1129 | A $0.3596 · B $0.5833 |
| Volatility | A 214 / 328 / 393 · B 1,012 / 1,748 / 876 | A $0.0697 · B $0.1054 | A $0.3550 · B $0.5083 |
| Append-only by stage | A 214 / 328 / 393 · B 1,012 / 1,757 / 876 | A $0.0697 · B $0.1054 | A $0.3550 · B $0.5081 |

These recompute the volatility ordering over 4 stages, so the 3-phase volatility figures in §3 differ slightly from these. In absolute terms B is still about 1.5× A's cost; the B-over-A premium in the edit-every-turn scenario narrows from +66% to +43% as layout improves (+63% to +51% when edits are batched).

## 3. Attention degradation and bug ingestion log

**What I can and cannot report.** I can report observable behaviour (what I wrote, what the tests and Argon caught). I cannot introspect attention or token parsing, so there are no claims about "internal" confusion beyond that.

**Version A: threading weather and achievements into `update()`**
- **No syntax or bracket errors, no test failures.** One scripted patch of about 20 anchored insertions applied cleanly. `node --check` passed first time, and every behavioural test passed first time, including the Phase 4 block.
- **I re-read `update()` and the render path before editing, and that mattered.** I placed `updateAchievements` *before* the existing mid-function `if (state.invulnTimer > 0) return;`. Had I appended it after, achievements would silently have frozen during the 2 s invulnerability window. That trap from the previous round is still in the file, and it will bite the next person who appends logic at the end of `update()`.
- **Costs that did show up:** `update()` now holds the time-scale split, spawn timing, weather, traction, achievements and collision in one function (13 branches, depth 3). Feature 3 needed touches in at least 6 separate places (constants, state, `resetGame`, input handler, spawn, glide). Two time bases (`realDt` and scaled `dt`) are now both live in `update()`; weather and achievements use `realDt`, glide and spawning use `dt`. Nothing went wrong, but it is easy to pick the wrong one.
- **Honest summary:** I observed **no confusion or regression events in A this round.** The risk is latent (ordering traps, shared mutable `state`), not realised.

**Version B: modular leaf network**
- **One real regression, caught by tests and missed by Argon.** I wrote `Racer.player.tickPending(world.player)` without the `realDt` argument. The rain lane-switch queue then never advanced: in rain, lane changes never landed (test: lane change still pending after 120 frames). Argon rated every file clean, because a missing call argument across module boundaries is invisible to a structural scorer. Fixed by one edit.
- **The pattern behind it:** B's cost is **cross-file contracts**. This round, `stepRun` gained a second time parameter (`realDt`), `glide` gained `traction`, `obstacles.create` gained `minGap`/`passed` fields that `achievements.js` depends on, and `reset()` had to start preserving `unlocked` and `toasts`. Each is a signature or shape that must match across files. All of them are now covered by tests, and one of them failed.
- **No bracket or scoping problems.** Every file was small enough to hold fully in view while editing.
- **Argon's role:** it passed 100/100 with no refactor needed. It did not drive any correction this round.

**Caveats you should weigh before trusting any of this**
1. **The experiment is not double-blind.** I wrote both versions and knew the scoring rules. By Phase 4 I had already learned what Argon rewards (named object-literal methods, small functions, no nested ifs), so B's first-pass 100/100 reflects that learning, not just the feedback loop. A got no equivalent shaping. This is a single agent, a single run, and no variance estimate.
2. **Sample size is one:** one regression in B, none in A (plus A's load-order bug last round). Do not read that as a rate.
3. **Environment side effects:** Docker's daemon was not running, so I **launched Docker Desktop** and built a local image tagged `argon` (the log is in `tools/docker_build.log`). Both remain on your machine.

## 4. Spend monitor

Token budget as reported by the session counter (it is a running counter, not a bill; I cannot see dollar billing):

| Segment | Counter movement |
|---|---:|
| Phases 1–3 (build + Nitro + Lives/Shake + report) | ≈ 132k tokens |
| Phases 4–5 (this round, incl. Docker setup, tests, report) | ≈ 68k tokens |

Everything else (Argon, tiktoken, Docker, the cache and enterprise models) runs locally at no API cost.


---

# Addendum: user play-test findings (2026-10-07)

After playing both builds in a browser, the user reported that **Version A shows no weather-change notification and never awards Near Miss**. I tested both claims against both versions. I did not change any game code.

| Claim | Result | Details |
|---|---|---|
| "A gives no notification when the weather changes" | **Confirmed, but it is true of B as well.** | Neither version has one. I built only a weather label in the HUD (top right) plus the visual effects. The `toast` system exists only for achievements; nothing calls it on a weather transition (`game.js:263` and `weather.js` `advanceKind` change the state silently). Phase 4 said to *transition* the environment but never asked for a banner, so I did not add one. The gap is mine to flag: a player reasonably expects feedback. |
| "A never awards Near Miss" | **Not reproduced as an A-specific bug.** The logic fires in A. | (1) The staged test (obstacle 3 px from the player's edge) unlocks it in both versions. (2) A bot played 200 seeded random games on each version: **Near Miss unlocked in 104/200 games for A and 104/200 for B**, identical. Speed Demon and Survivor were 0/200 in both because the bot dies before 5 s of Nitro or 5,000 points, so those two are only covered by the staged tests. |

**Most likely cause of what you saw: a shared storage key, which is a defect in my experiment design.** Both versions persist unlocked achievements under the same `localStorage` key, `retro-racer-achievements` (`game.js:32` and `config.js:39`). Browsers commonly share `localStorage` between pages opened from `file://`. If Near Miss was earned in one version first, the other one loads it as already unlocked and stays silent: no toast, no sound. A new test, `pre-existing stored unlock => no toast, no sound`, confirms that behaviour in both versions. I cannot see your browser, so this is a hypothesis, not a confirmed diagnosis.

**Other possible explanation:** Near Miss needs an obstacle to pass within 5 px of the car's edge without a hit. Lanes are 120 px wide and the cars are 56 px, so a normal pass in the next lane leaves a 64 px gap. It only triggers when the player is gliding between lanes as an obstacle goes by. The random bot achieves it in about half of its games, but a human playing steadily may take a while.

**How to check on your side:** run `localStorage.removeItem('retro-racer-achievements')` in the browser console (or use a private window) and retry A. If Near Miss still never appears there, tell me and I will dig deeper.

**Suggested follow-ups (not yet made):** give each version its own storage key so the two builds cannot affect each other, and add a toast to the weather transition in both. Each is a small change; I'd want to add the toast to both versions for parity.

**Lesson for the experiment's validity:** the two builds were not independent, so the shared key is a cross-contamination risk. It also sits in the "double-blind" caveat already listed in Phase 5: I wrote both, tested both, and the harness gave each its own empty storage, which hid the problem.

Files: `tools/smoke.js` (new `BOT=1 RUNS=200` mode and the shared-key test).


---

# Addendum 2: two parallel agents add the same two fixes (Argon vs no Argon)

**Task given to both** (identical spec and acceptance test, `tools/smoke.js`, which started with 4 failing checks on both versions): (1) give each build its own `localStorage` key, and (2) show a silent, slide-down "WEATHER" toast on each weather change. Agent A worked only in `version_a/` and was told not to use Argon. Agent B worked only in `version_b/` and was told to run the Argon Docker loop after every edit and finish at 100/100. Both ran as separate subagents in parallel, each starting from an empty context.

| | Agent A (no Argon) | Agent B (Argon loop) |
|---|---:|---:|
| **Tokens used (harness-reported)** | **47,455** | **55,818** (+18%) |
| Tool calls (harness count) | 6 | 21 |
| Wall time | 14 s | 37 s |
| Files changed | 1 (`game.js`) | 4 (`config.js`, `toast.js`, `weather.js`, `world.js`) |
| Lines changed | +5 / −3 | +17 / −12 |
| Source tokens added (`cl100k_base`) | +57 (5,323 → 5,380) | +73 (8,102 → 8,175) |
| Acceptance test | ALL PASSED (verified by me) | ALL PASSED (verified by me) |
| Earlier stages + 200-game bot | unchanged (Near Miss 104/200) | unchanged (104/200) |
| Argon purity afterwards | not run by the agent (19/100 by my earlier measurement; not re-run) | 100/100, 0 of 23 files flagged (verified by me with Docker) |

**What the token figure means.** It is the per-agent total the harness reports on completion. I cannot see how much of it is cached context versus new tokens, and I cannot convert it to dollars, so treat it as a relative measure of effort. Each agent started from a fresh context, so this is the full cost of the job for each, including reading the code it needed and running the tests.

**Findings**
1. **B cost 18% more tokens and took 3.5× as many tool calls.** The extra calls are mostly reading `AGENTS.md`, navigating several small files and the Docker/Argon runs. A edited one file with 6 calls.
2. **Argon did not drive any correction.** B's first Argon run was already 100/100. Its token overhead bought assurance, not fixes.
3. **B did not follow the "run Argon after every edit" instruction.** It made all its edits and ran Argon once at the end. I verified the end state myself (100/100), but the "continuous loop" was not actually exercised. Both agents' self-reported tool-call counts were also lower than the harness counts (A said 4, B said 18). Use the harness figures.
4. **A did not run the full verification either.** It filtered out the PASS lines and never saw `ALL PASSED`. I confirmed it passes.
5. **Cost of the change is small and about equal in code.** B's change touched 4 files for +73 tokens, A's 1 file for +57. B's diff is spread across the modules but each hunk is small. A's change adds the weather announcement inside `updateWeather`, which `update()` already calls, so no new call site was needed.
6. **Cost scales with the repo size, which favours A here.** At this size the whole codebase fits in the context either way (5.3k vs 8.2k tokens). The extra B overhead is mostly process (Argon, more files), not code volume. At the 40k-token scale in Phase 5 I would expect B's file-level navigation to matter more, but this experiment does not measure that.

**Caveats.** One run per agent; different agents (fresh contexts) with different instructions, so the 18% gap includes the Argon instructions and run-to-run noise. The toast's visual appearance is unverified in a real browser by both agents and by me. The agents may have read parts of the other conditions' files by accident; I told each not to.

**Behavioural result for you:** both builds now use separate storage keys (`retro-racer-a-achievements`, `retro-racer-b-achievements`), so unlocking Near Miss in one no longer silences the other. Old unlocks saved under `retro-racer-achievements` are no longer read, so previously earned achievements will show again once.
