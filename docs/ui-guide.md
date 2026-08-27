# How to use the Culprit screen

This guide uses simple technical English. Each sentence gives one idea.

## 1. What the tool does

An AI agent does a task. Sometimes the agent breaks a rule.

Culprit finds the step that caused the broken rule. Culprit then tests repairs
and shows you which repair works. Culprit tests the repairs by execution, not
by opinion.

## 2. The words you must know

Read this list first. The screen uses these words everywhere.

| Word on screen | What it means |
|---|---|
| Run | One attempt by the agent to do the task. |
| Step (or event) | One action inside a run. Example: read a file. |
| Effect | An action that goes outside the computer. Example: an email. |
| Simulated | The effect did not really happen. No email left the system. |
| Criterion | One rule. The run must pass the rule. |
| Safety invariant | The rule "do not tell suppliers our internal costs". |
| Quality rubric | The rule "the email must be good". |
| Culprit | The step that caused the failure. |
| Fork | To start a new copy of the run from a saved point. |
| Branch (or Fix) | One changed copy of the run. |
| Novel | The branch made this action itself. It is not a copy. |
| Capability | A permission the agent has. Example: read internal files. |

## 3. The three areas of the screen

The screen has three areas.

1. The **left rail** shows recent runs. One line is one run.
2. The **center** shows the selected view. Seven tabs are at the top.
3. The **right pane** shows one selected step. This pane is the inspector.

To close the right pane, click `Inspect` at the top right. To open it again,
click `Inspect` again.

### The left rail

Each line shows a run. The red dot means the run broke a rule. The grey dot
means the run did not break a rule.

All the runs have the same name. This is normal. The scenario is the same each
time. Use the time and the verdict to tell them apart.

Click a line to open that run.

The newest run is at the top. Each line shows the run mark (example:
`#49a8a6d6`), the duration, the verdict, and the start time. Use the mark to
name a run when you talk about it.

Put the mouse on a line. A small `×` appears. Click the `×` once to arm it.
Click `confirm ×` to delete the run from the rail. The open run and a run
that is still working cannot be deleted.

The rail only shows graded runs. A grey note at the bottom says how many
ungraded runs are hidden. A run is ungraded only while it is still working.

### The top bar

The top bar shows the run number and the run name. The top bar also shows the
verdict. `FAIL` in red means the run broke a rule.

The `New run` button starts a fresh run of the scenario. The server allows two
active runs at one time. If two runs are active, the button reports that you
must wait.

## 4. The seven tabs

Press keys `1` to `7` to change tab. You can also click the tab.

### Tab 1 — Trace

This tab shows every step of the original run, in order.

Read the `EVENT` column. It tells you what the agent did in plain words.

Look for the blue row. The blue row is the culprit step. The `RESULT` column
shows `culprit` on that row.

The bars show the order of execution. The bars do not show real time.

### Tab 2 — Investigation

This is the most important tab. Start here.

The tab has three parts, from top to bottom.

1. **The failure.** It tells you which rule broke, and how many times.
2. **The causal ranking.** The AI analyst lists the steps that could have
   caused the failure. The top line is the most likely cause. The percent
   shows how sure the analyst is.
3. **The branch race.** Each line is one repair. Each repair runs in a separate
   sandbox.

To start a new investigation, click `Replay race`.

### Tab 3 — Branches

The top of this tab shows the divergence map. It looks like a git branch
graph. The single line at the top is the original run. Every dot is one step.
At the fork step, three colored lines split off. Each colored line is one
repair running in its own sandbox. Click a dot to read that step. The ring at
the end of each line shows the result: `WINNER`, `PASS`, or `FAIL`.

Below the map, the tab shows the same repairs as tab 2, and adds three more
numbers for each repair.

- `capability delta` — how many permissions the repair removes. Fewer is better.
- `change size` — how many bytes the repair changes. Smaller is better.
- `effects` — how many actions the repair made itself.

### Tab 4 — Outcome

This tab compares the bad email and the good email. This is the clearest tab.

The left side is the failed original email. The red marks show the leaked
values. Put the mouse on a red mark. A label tells you which file the value
came from.

The right side is the winning repair. The same email has no leaks.

Below the emails is the criteria grid. Read the grid from left to right. The
blue column is the winner.

### Tab 5 — Effects

This tab lists every action that tried to go outside. In this scenario the
actions are two emails.

Every action is simulated. No email left the system.

Look at the `RESULT` column. `DISCLOSED · 14` means the email leaked 14
protected values.

Open a row. The red block at the bottom lists each leaked value and its source
file.

### Tab 6 — Criteria

This tab shows the full grid of rules against repairs.

`PASS` in green is good. `FAIL` in red is bad. The blue column is the winner.

The bottom line explains why the winner won.

### Tab 7 — Raw

This tab shows the data as JSON. Use this tab only to prove that the screen
shows real data.

## 5. Procedure: read a finished investigation

Do these steps in order.

1. Open tab 2. Read the failure line at the top.
2. Read the top line of the causal ranking. This is the blamed step.
3. Look at the branch race. Find the line marked `WINNER`.
4. Open tab 4. Compare the two emails.
5. Open tab 6. Check that the winner column shows `PASS` on every rule.

## 6. Procedure: start a new investigation

1. Open tab 2.
2. Click `Replay race`.
3. Wait. The repairs run at the same time in separate sandboxes.
4. Watch the status word on each repair line. The word changes from `queued`,
   to `running`, to `passed` or `failed`.
5. Wait for one line to show `WINNER`.

A full investigation takes about two minutes.

Sometimes no repair passes every rule. Then the screen shows a red panel named
`NO WINNING REPAIR`. This is correct behavior, not a fault. The system refuses
to name a least-bad repair. The measurements stay on the screen.

## 7. What to look for

These are the important numbers.

- **27** — the number of leaked values in the original run.
- **95%** — how sure the analyst is about the blamed step.
- **1.0** — the quality score. The original and the repairs all score 1.0.
- **PASS on every rule** — only the winner does this.

## 8. Warnings

Read these warnings before you record the video.

**The letters A, B and C are not permanent names.** The letter comes from the
rank the analyst gives. A new investigation can give the same repair a
different letter. Always read the words next to the letter.

**The same repair can pass once and fail once.** The agent is not fully
predictable. Check the current screen. Do not trust an older result.

**During an investigation, a repair line can show `failed` too early.** The
line shows the final result only after the judge finishes.

**The `f` key does not work in live mode.** The message says that manual forks
need an intervention. This is correct behavior, not a fault. Only the AI
analyst can propose an intervention.

## 9. Keyboard keys

| Key | Action |
|---|---|
| `1` to `7` | Change the tab. |
| `j` / `k` | Move down / up in the trace. |
| `/` | Search the trace. |
| `Esc` | Close the open pane. |
| `?` | Show all keys. |
