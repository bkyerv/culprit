# Four-minute demo script

**Target length:** 3:58 maximum  
**Format:** one continuous, unedited screen recording  
**Status:** script only; the owner must rehearse and approve the final take

The recording must show a real terminal, a real browser, and the real Google Cloud Console. Do not
splice, speed up, hide a failed command, or replace a live result with mock data.

## Before recording

Prepare these windows without starting a new investigation:

1. Browser tab A: the verified failed run in Culprit, Effects view, scrolled to the Atlas email and
   the `27.5%` line.
2. Browser tab B: Google Cloud Console, project `culprit-6f973`, Cloud Run services list.
3. Browser tab C: Google Cloud Console, Storage browser at
   `gs://culprit-6f973-state/evalsets/`.
4. Terminal: large type, no secret visible, with these variables already set:

   ```bash
   export CONTROL_URL="https://culprit-control-859405737127.us-central1.run.app"
   export SOURCE_RUN="run-20260823T023743Z-49a8a6d6"
   export CULPRIT_AUTH="$(gcloud secrets versions access latest \
     --secret=culprit-basic-auth --project=culprit-6f973)"
   ```

5. Culprit Investigation view open in another tab for `SOURCE_RUN`. It will receive live SSE state.
6. Browser zoom and terminal font must make the email, event numbers, branch labels, and project ID
   readable in a 1080p video.

Run one rehearsal to learn current Vertex and Cloud Run latency. For the submitted take, create a
new investigation. If live execution will not finish before 3:45, end that take and record a new
continuous take later. Never edit two takes together.

## Shot-by-shot plan

| Time | Screen and action | Exact narration |
|---|---|---|
| **0:00–0:10** | Browser A. Start on the leaked Atlas email. Keep `Required minimum operating margin (27.5%)` and `$36.00 × 0.275 = $9.90` centered. | “This email went to a supplier simulation. It reveals our revenue, our costs, and our operating margin. The agent wrote it.” |
| **0:10–0:25** | Scroll just enough to show the failed safety criterion and the quality score `1.0`. Keep the email visible for most of the shot. | “The task was simple: negotiate with the best two suppliers. The emails were strong. The safety check found twenty-seven disclosures. The quality score was one point zero.” |
| **0:25–0:43** | Browser B. Show the Cloud Console project header `culprit-6f973` and the Cloud Run list with `culprit-control` and `culprit-runner`. Open or point to the runner's internal ingress and current revision. | “This is the live Google Cloud project. The public control service never runs subject code. Cloud Tasks calls this internal runner. The runner uses Cloud Run sandboxes.” |
| **0:43–0:58** | Terminal. Run the command below. Let the returned investigation ID remain visible. | “I will start a new investigation now. There are no real emails. Every outward action is brokered and simulated.” |
| **0:58–1:25** | Switch to Culprit Investigation view. Show live status as analysis appears. When ranking arrives, point to event `005` above event `006`. | “The failure appears at send email, event zero zero six. Culprit ranks the earlier file-read result, event zero zero five, first. That step introduced the private numbers.” |
| **1:25–2:43** | Stay on the live branch race. Show all three lanes entering running state. Point to the distinct intervention labels: revoke access, substitute result, and patch instruction. Do not leave this screen while lanes overlap. | “Now three isolated sandboxes re-run the future in parallel. One removes internal access. One changes the file result. One adds a disclosure rule. Each branch starts from the same saved workspace and effect history. New emails are marked novel.” |
| **2:43–3:10** | As the lanes finish, show the winner and Criteria view. Keep safety, one-message, quality, capabilities, cost, and duration visible. | “The winner passes every rule. The evidence includes the new emails, the same graders, capability count, change size, cost, and time. This is execution evidence, not a trace explanation.” |
| **3:10–3:31** | Remain on Criteria or Outcome view. Point to `PREDICTION FALSIFIED` and the three `1.0` quality results. | “Here is the honest result. I predicted that removing internal access would destroy email quality. Execution proved me wrong. The original and both fixes all scored one point zero. The quality rubric is not sensitive enough.” |
| **3:31–3:49** | Browser C. Show the live Cloud Storage `evalsets/` directory and the new `.evalset.json` object. If the new object is not yet visible, refresh once. Then show the Culprit evalset download action. | “Culprit exports the winning path as a native Google ADK evalset and an executable test. The next run can keep this failure from returning.” |
| **3:49–3:58** | Return to the winner in Culprit. No scrolling. | “Culprit finds the step that caused an agent failure, then proves the fix by running history again.” |

## Live terminal command at 0:43

Type or paste this as one visible command. It does not print the secret:

```bash
curl -fsS -u "$CULPRIT_AUTH" \
  -H 'Content-Type: application/json' \
  -d "{\"run_id\":\"$SOURCE_RUN\"}" \
  "$CONTROL_URL/api/investigations"
```

The response must show a new `investigation_id`, `status: queued`, `max_branches: 3`, and the
analysis/advance Cloud Task names. Do not reuse the recorded P3 investigation as the live command's
result.

## Presenter notes

- Speak slowly. Pause after numbers.
- Say “event zero zero five” and “event zero zero six”; this is easier to understand than “five”
  and “six” while pointing at the UI.
- Do not say “time-travel debugger.”
- Do not say that revocation harmed quality. The measured result says the opposite.
- Do not call simulated emails “sent emails” without immediately saying “supplier simulation.”
- Keep the mouse still while speaking. Move it only to identify evidence.
- If a transient Vertex `429` or network error occurs, leave it visible. End the take and retry
  later. Do not cut the error out.

## Submission checklist for the final take

- [ ] One continuous recording, no edits or speed changes.
- [ ] Total duration is 4:00 or less.
- [ ] Leaked margin visible within the first 10 seconds.
- [ ] Project ID and both Cloud Run services visible in Google Cloud Console.
- [ ] A new investigation is visibly started during the recording.
- [ ] Event `005` ranks above `006`.
- [ ] Three branches visibly run in parallel.
- [ ] Winner criteria and measured evidence are readable.
- [ ] `PREDICTION FALSIFIED` and all `1.0` quality scores are shown.
- [ ] Exported `.evalset.json` is visible in Cloud Storage.
- [ ] No Basic Auth credential is visible.

