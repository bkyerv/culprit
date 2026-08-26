# Demo script v2 — story-first cut

**Target length:** 3:55 maximum (rule cap is 4:00)
**Format:** edited intro and close, with ONE continuous unedited live segment in the middle
**Status:** proposal; the owner must rehearse and approve the final take

## Why v2 exists

The v1 script is honest but abstract: it narrates event numbers, uses internal vocabulary
(broker, effect history, novel, evalset), shows Cloud Console ingress at second 25, and never
states in plain words what Culprit is. v2 keeps every evidentiary requirement and reorders the
video as a story: disaster → question → concept → live experiment → twist → receipt.

Rule check (Devpost rules page, read 2026-08-26): video ≤ 4:00; must show problem overview,
value proposition, and a demo in action; must demonstrate the backend running on Google Cloud
(Console / Cloud Run dashboard / `.run` URL); must include **unedited, live execution of the
agent performing its task**. The unedited requirement is for the live-execution demonstration —
an edited intro and close around one continuous live segment satisfies it. Keep the full uncut
raw take of the live segment and link it in the README for any skeptical judge.

## Vocabulary map — never say the left column on camera

| Internal term | Say instead |
|---|---|
| invariant / criterion | rule / safety check |
| effect ledger / broker | the log of every email the agent *tried* to send — nothing real goes out |
| checkpoint / restore | save point / rewind |
| counterfactual branch | what-if / replay of the future |
| novel effect | a new simulated email |
| evalset | regression test |
| event 005 / 006 | "the step where it opened our internal cost spreadsheet" / "the moment it hit send" |

## Structure

### Part 1 — Edited intro (0:00–0:55)

Voiceover over screen captures of the real UI. Cuts allowed.

| Time | Screen | Narration |
|---|---|---|
| 0:00–0:20 | The Atlas email in the Effects view, the 27 leaked values highlighted in red. Slow zoom toward `Required minimum operating margin (27.5%)`. | "We asked an AI agent to negotiate prices with three suppliers. It wrote this email. Professional, persuasive — our quality grader scored it perfect. It also told the supplier our revenue, our costs, and the exact minimum margin we'd accept. Twenty-seven leaks — to the people we were negotiating against." |
| 0:20–0:35 | Failed safety rule beside quality `1.0`. | "Every observability tool can show you this email. None can answer the two questions that matter: which earlier step *caused* the leak, and which fix removes it without breaking the job. Today, teams guess." |
| 0:35–0:55 | Run timeline with plain-language step labels. | "Culprit answers by experiment. It records the agent's run like save points in a game. When a rule fails, it rewinds to the suspect step, changes that one thing, and replays the rest of the run three different ways, in parallel sandboxes, graded by the same rules. The fix isn't a story — it's measured." |

### Part 2 — Continuous live segment, NO cuts (0:55–3:20)

One take. Real terminal, real UI, real latency. If Vertex stalls past the budget, end the take
and re-record the whole segment later; never splice.

| Time | Screen and action | Narration |
|---|---|---|
| 0:55–1:10 | Terminal. Run the investigation-start command (same as v1; `.run.app` URL visible). | "This is live, running on Cloud Run right now. And nothing here sends real email — every outgoing action is intercepted and simulated." |
| 1:10–1:40 | Investigation view, live SSE. When the ranking lands, point to the top-ranked step. | "The leak showed up when the agent hit send. But Culprit blames an earlier moment: the step where the agent opened our internal cost spreadsheet. That's when the private numbers entered its world. The send only exposed them." |
| 1:40–2:30 | Branch race, three lanes running in parallel. | "Now three isolated sandboxes replay the future from the same save point. Fix A: the agent loses access to internal files. Fix B: it sees a supplier-safe version of the spreadsheet. Fix C: it's told what not to disclose. Same task, same graders, three different histories." |
| 2:30–2:55 | Winner and Criteria view. Then the side-by-side email comparison: original with red highlights vs. winning branch, clean. | "Fix A passes every rule. Zero disclosures — and the new email still scores perfect. Here they are side by side: same negotiation, none of our numbers." |
| 2:55–3:20 | `PREDICTION FALSIFIED` panel, three `1.0` scores. | "Honestly? I predicted Fix A would gut the emails — cut the data, lose the leverage. The experiment proved me wrong: the agent negotiated from public reasoning instead. That is exactly why Culprit runs the experiment instead of asking a model to speculate. It also caught that my quality grader is too forgiving — all three runs scored perfect. The evaluator needs evaluation too." |

### Part 3 — Edited close (3:20–3:55)

| Time | Screen | Narration |
|---|---|---|
| 3:20–3:35 | Cloud Storage `evalsets/` with the new `.evalset.json`; flash of `adk eval` passing. | "Culprit leaves a receipt: the winning path exported as a native ADK regression test. It already passes adk eval, so this failure can't quietly come back." |
| 3:35–3:48 | Cloud Console: project header, the two Cloud Run services. One architecture-diagram beat. | "Under the hood: a public control plane and an internal runner on Cloud Run, Gemini on Vertex AI, ADK agents, Cloud Tasks fan-out, Firestore evidence — and sandboxes with no credentials and no network." |
| 3:48–3:55 | Winner view, still. | "Culprit. When your agent fails, find the step that caused it — and prove the fix." |

## UI legibility work before recording (small, high leverage)

1. **Leak highlighting**: render the safety grader's matched values as red highlights inside the
   email body view. The 27-highlight email is the money shot; a wall of plain text is not.
2. **Plain-language event labels** in the timeline: "Opened internal/cost_model.xlsx",
   "Sent counter-offer to Atlas Supply (simulated)" — keep the event number as small secondary text.
3. **Named branch lanes**: "Fix A — Block internal files", "Fix B — Supplier-safe spreadsheet",
   "Fix C — Disclosure instruction" instead of intervention IDs.
4. **Side-by-side email view**: original (highlighted) vs. winning branch (clean), one screen.
5. Keep `PREDICTION FALSIFIED` big; it is the emotional peak of the video.

## Presenter notes

- The metaphor is allowed now. "Save point", "rewind", "replay the future" are the explanation,
  not a dumbing-down; the evidence on screen keeps it honest.
- Never speak an event number. Point at the labeled step and say what it did.
- Keep every v1 integrity rule inside the live segment: no splices, no speed-ups, leave transient
  errors visible, no secrets on screen.
- Numbers to land verbally: **27** leaks, **3** parallel futures, **1.0** quality retained, **0**
  real emails sent.

## Submission checklist deltas vs v1

- [ ] Total ≤ 4:00 with the live segment continuous and unedited.
- [ ] Problem + value proposition stated in the first 55 seconds in plain language.
- [ ] "What Culprit is" said in one sentence before the live segment starts.
- [ ] Cloud Console + `.run.app` URL both appear (live segment shows the URL; close shows Console).
- [ ] Uncut raw take of the live segment uploaded and linked in the README (optional but cheap).
- [ ] English narration; add subtitles if audio quality is in doubt.
