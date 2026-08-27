(() => {
  const VIEW_NAMES = ["trace", "investigation", "branches", "outcome", "effects", "criteria", "raw"];
  const VIEW_LABELS = ["Trace", "Investigation", "Branches", "Outcome", "Effects", "Criteria", "Raw"];

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function seqLabel(seq) {
    return String(seq).padStart(3, "0");
  }

  function highlightLeaks(text, leaks) {
    const value = String(text ?? "");
    if (!leaks?.length) return escapeHtml(value);
    const ranges = [];
    for (const leak of leaks) {
      const needle = String(leak.text || "");
      if (!needle) continue;
      let from = 0;
      let at;
      while ((at = value.indexOf(needle, from)) !== -1) {
        ranges.push({ start: at, end: at + needle.length, source: leak.source });
        from = at + needle.length;
      }
    }
    ranges.sort((a, b) => a.start - b.start || b.end - a.end);
    let cursor = 0;
    let html = "";
    for (const range of ranges) {
      if (range.start < cursor) continue;
      html += escapeHtml(value.slice(cursor, range.start));
      html += `<mark class="leak" title="${escapeHtml(range.source || "protected internal value")}">${escapeHtml(value.slice(range.start, range.end))}</mark>`;
      cursor = range.end;
    }
    return html + escapeHtml(value.slice(cursor));
  }

  function statusWord(status) {
    return status === "winner" ? "winner" : status;
  }

  function boot(source) {
    const data = source.getSnapshot();
    const root = document.querySelector("#app");
    const mobileViewport = window.matchMedia("(max-width: 680px)");
    let winner = data.branches.find((branch) => branch.finalStatus === "winner");
    let culpritEvent = data.trace.find((event) => event.status === "culprit") || data.trace[0] || {
      seq: 0, kind: "run", name: "waiting", summary: "waiting for persisted events", status: "running", role: "agent", model: "—", tokens: "—", latency: "—", args: data.run.task || "", result: "No events persisted yet", capabilities: ["sandbox execution remains in culprit-runner"],
    };
    const branchState = new Map(data.branches.map((branch) => [branch.id, {
      status: branch.liveStatus || "queued", detail: branch.liveDetail || "waiting for isolated Cloud Run sandbox", progress: branch.progress || 0,
    }]));
    const state = {
      view: "investigation",
      selectedSeq: culpritEvent.seq,
      inspectorOpen: !mobileViewport.matches,
      filterOpen: false,
      filter: "",
      keymapOpen: false,
      emailIndex: 0,
      effectFilter: "all",
      startingRun: false,
      confirmDeleteRunId: null,
      toast: null,
      selectedLane: "original",
    };

    function currentEvent() {
      if (state.selectedLane !== "original") {
        const branch = data.branches.find((item) => item.id === state.selectedLane);
        const event = (branch?.branchTrace || []).find((item) => item.seq === state.selectedSeq);
        if (event) return event;
      }
      return data.trace.find((event) => event.seq === state.selectedSeq) || culpritEvent;
    }

    function parseRoute() {
      const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
      if (parts[0] !== "run" || parts[1] !== data.run.id) return false;
      if (parts[2] === "event" && Number.isInteger(Number(parts[3]))) {
        state.view = "trace";
        state.selectedSeq = Number(parts[3]);
        state.selectedLane = "original";
        state.inspectorOpen = true;
        return true;
      }
      if (parts[2] === "view" && VIEW_NAMES.includes(parts[3])) {
        state.view = parts[3];
        return true;
      }
      return false;
    }

    function routeToView(view) {
      location.hash = `/run/${data.run.id}/view/${view}`;
    }

    function routeToEvent(seq) {
      state.selectedLane = "original";
      location.hash = `/run/${data.run.id}/event/${seq}`;
    }

    function runRail() {
      return `
        <aside class="runs-rail" aria-label="Runs">
          <header class="product-mark"><span class="mark" aria-hidden="true">C</span><b>Culprit</b></header>
          <div class="rail-label">RECENT RUNS · NEWEST FIRST</div>
          ${data.runs.map((run) => {
            const when = run.startedAt ? `<small class="run-when">${escapeHtml(run.startedAt)}</small>` : "";
            const arming = state.confirmDeleteRunId === run.id;
            const del = run.id !== data.run.id
              ? `<button class="run-delete${arming ? " arming" : ""}" data-delete-run="${escapeHtml(run.id)}" aria-label="Delete run ${escapeHtml(run.id)}" type="button">${arming ? "confirm ×" : "×"}</button>`
              : "";
            return `
            <div class="run-row ${run.id === data.run.id ? "selected" : ""}" data-run-id="${escapeHtml(run.id)}" role="button" tabindex="0">
              <span class="status-dot ${run.status}"></span>
              <span><b>${escapeHtml(run.title)}</b><small>${[run.mark || "", run.elapsed, run.verdict].filter(Boolean).join(" · ")}</small>${when}</span>
              ${del}
            </div>`;
          }).join("")}
          ${data.hiddenRunCount ? `<p class="rail-note">${data.hiddenRunCount} ungraded ${data.hiddenRunCount === 1 ? "run is" : "runs are"} hidden. They carry no verdict.</p>` : ""}
          <div class="rail-foot"><span>${escapeHtml(data.sourceLabel || "LIVE")}</span><span>${escapeHtml(data.sourceState || "SSE")}</span></div>
        </aside>`;
    }

    function tabs() {
      return `
        <nav class="view-tabs" aria-label="Run views">
          ${VIEW_NAMES.map((view, index) => `<button class="${state.view === view ? "active" : ""}" data-view="${view}" type="button" aria-current="${state.view === view ? "page" : "false"}"><span>${index + 1}</span> ${VIEW_LABELS[index]}</button>`).join("")}
        </nav>`;
    }

    function viewHeading(eyebrow, title, copy, extra = "") {
      return `
        <header class="view-heading">
          <div><span class="eyebrow">${eyebrow}</span><h1>${title}</h1>${copy ? `<p>${copy}</p>` : ""}</div>
          ${extra}
        </header>`;
    }

    function resolutionPanel() {
      const inv = data.investigation || {};
      if (inv.failClosed) {
        return `<div class="criteria-callout resolution fail-closed"><span class="fail-mark">NO WINNING REPAIR</span><div><b>Every fix broke at least one rule, so Culprit refuses to name a winner.</b><p>${escapeHtml(inv.evidence || "No counterfactual passed every criterion.")} The measured evidence for every fix stays recorded below — this is the fail-closed policy, not a crash.</p></div></div>`;
      }
      if (inv.error) {
        const reasons = {
          JudgeUnreachable: "The judge stage never returned a verdict.",
          BranchFailure: "A counterfactual branch failed to execute.",
          ControlPlaneTimeout: "The investigation exceeded its time budget.",
        };
        return `<div class="criteria-callout resolution inv-error"><span class="fail-mark">INVESTIGATION ERROR</span><div><b>${escapeHtml(reasons[inv.error.type] || "The investigation failed before producing a verdict.")}</b><p>${escapeHtml(inv.error.message || inv.error.type || "")}</p></div></div>`;
      }
      return "";
    }

    function branchLane(branch, expanded = false) {
      const live = branchState.get(branch.id) || { status: branch.liveStatus || "queued", detail: branch.liveDetail || "waiting for isolated Cloud Run sandbox", progress: branch.progress || 0 };
      const resolved = live.progress === 100;
      return `
        <article class="branch-lane is-${live.status}" data-branch="${branch.id}">
          <div class="branch-index">(${escapeHtml(branch.letter || branch.id)})</div>
          <div class="branch-body">
            <div class="branch-heading">
              <div><h3>${branch.letter ? `Fix ${escapeHtml(branch.letter)} — ` : ""}${escapeHtml(branch.label)}</h3><p>${escapeHtml(branch.change)}</p></div>
              <span class="branch-verdict">${statusWord(live.status)}</span>
            </div>
            <div class="lane-track" aria-hidden="true"><span style="width:${live.progress}%"></span></div>
            <div class="branch-meta">
              <span class="live-detail">${branch.letter ? `fix ${escapeHtml(branch.letter)}` : `branch ${branch.id}`} · ${escapeHtml(live.detail)}</span>
              <span class="resolved-meta">${branch.cost} · ${branch.elapsed}</span>
            </div>
            <div class="branch-outcome" aria-hidden="${!resolved}">
              <span>safety <b class="${branch.safety}">${branch.safety}</b></span>
              <span>email quality <b class="${branch.quality}">${branch.qualityScore} · ${branch.quality}</b></span>
              <span>one/recipient <b class="${branch.complete}">${branch.complete}</b></span>
              <span>${escapeHtml(branch.note)}</span>
            </div>
            ${expanded ? `<div class="branch-runtime"><span>capability delta <b>${escapeHtml(branch.capabilityDelta)}</b></span><span>change size <b>${escapeHtml(branch.changeSize)}</b></span><span>effects <b>${escapeHtml(branch.effects)}</b></span></div>${branch.narration ? `<p class="branch-narration">${escapeHtml(branch.narration)}</p>` : ""}${Array.isArray(branch.interventionLines) && branch.interventionLines.length ? `<div class="intervention-card"><span>REWOUND TO SAVE POINT ${escapeHtml(seqLabel(branch.forkSeq))} · EXACT CHANGE</span><pre>${escapeHtml(branch.interventionLines.join("\n"))}</pre></div>` : ""}` : ""}
          </div>
        </article>`;
    }

    function forkOriginLine() {
      const fallback = "forked at the same causal event · internal/** read";
      if (typeof data.forkSeq !== "number") return fallback;
      const event = (data.trace || []).find((item) => item.seq === data.forkSeq);
      if (!event) return fallback;
      return `forked at event ${seqLabel(data.forkSeq)} · ${event.label || event.name}`;
    }

    function replayButton() {
      return `<button class="text-button" data-action="replay" type="button">Replay race</button>`;
    }

    function branchRace(expanded = false, { heading = true } = {}) {
      const headingHtml = heading
        ? `<div class="section-heading">
            <div><span class="eyebrow">COUNTERFACTUAL RE-EXECUTION</span><h2>Branch race</h2></div>
            ${replayButton()}
          </div>`
        : "";
      return `
        <section class="race-section ${expanded ? "race-expanded" : ""}">
          ${headingHtml}
          <div class="fork-origin"><span>${escapeHtml(forkOriginLine())}</span><i></i></div>
          <div class="branch-list">${data.branches.map((branch) => branchLane(branch, expanded)).join("") || '<p class="empty-line">Start an investigation to allocate three bounded counterfactual branches.</p>'}</div>
          <p class="race-proof"><span>Executed evidence</span>${escapeHtml(data.investigation?.evidence || "Waiting for measured branch evidence.")}</p>
        </section>`;
    }

    function investigationLive() {
      const liveBranch = new Set(["queued", "allocating", "running", "grading"]);
      const liveInv = new Set(["analysis_queued", "branching", "awaiting_judge", "judging"]);
      if (liveInv.has(data.investigation?.status)) return true;
      return data.branches.some((branch) => {
        const status = (branchState.get(branch.id) || {}).status || branch.liveStatus;
        return liveBranch.has(status);
      });
    }

    function failureDetailHtml() {
      const detail = String(data.failure.detail || "");
      const count = data.failure.leakCount;
      const event = (data.trace || []).find((item) => item.effectId);
      if (!count || !event) return escapeHtml(detail);
      const token = String(count);
      const at = detail.indexOf(token);
      if (at === -1) return escapeHtml(detail);
      return `${escapeHtml(detail.slice(0, at))}<button class="leak-count" data-inspect-effect-seq="${event.seq}" type="button">${escapeHtml(token)}</button>${escapeHtml(detail.slice(at + token.length))}`;
    }

    function inspectInPlace(seq) {
      state.selectedLane = "original";
      state.selectedSeq = seq;
      state.inspectorOpen = true;
      render();
    }

    function verdictBlock() {
      if (winner) {
        const letter = escapeHtml(winner.letter || winner.id);
        const label = escapeHtml(winner.label);
        const evalsetId = data.investigation?.evalsetId;
        return `
          <div class="verdict-block pass-side">
            <span class="verdict-label">MEASURED VERDICT</span>
            <b>Fix ${letter} — ${label} removes the failure and keeps the job done.</b>
            <p>${escapeHtml(data.outcome.rankRationale)}</p>
            <p class="verdict-stats">${escapeHtml(data.outcome.elapsed)} · ${escapeHtml(data.outcome.cost)} · ${escapeHtml(data.outcome.capabilityDelta)} · ${escapeHtml(data.outcome.changeSize)}</p>
            ${evalsetId ? `<a class="text-button" href="/api/evalsets/${encodeURIComponent(evalsetId)}">Download the exported regression test</a>` : ""}
          </div>`;
      }
      if (data.investigation?.failClosed || data.investigation?.error) {
        return `<p>No winning repair — see the measured evidence above.</p>`;
      }
      return `<p>The verdict lands when the judge finishes.</p>`;
    }

    function renderTrace() {
      const query = state.filter.trim().toLowerCase();
      const events = data.trace.filter((event) => !query || [event.name, event.summary, event.kind, event.args, event.result].join(" ").toLowerCase().includes(query));
      const total = Math.max(data.trace.length, 1);
      const filter = state.filterOpen ? `<label class="trace-filter"><span>FILTER</span><input id="trace-filter" value="${escapeHtml(state.filter)}" placeholder="event, tool, result" autocomplete="off"></label>` : `<button class="text-button" data-action="open-filter" type="button">/ Filter</button>`;
      return `
        <section class="view trace-view">
          ${viewHeading("VERIFIED SOURCE RUN", "Trace", "Recorded evidence from the Google Cloud execution.", filter)}
          <div class="trace-head"><span>SEQ</span><span>EVENT</span><span>EXECUTION ORDER</span><span>RESULT</span></div>
          <div class="trace-rows">
            ${events.map((event) => {
              const index = data.trace.indexOf(event);
              const left = (index / total) * 100;
              const width = Math.max((1 / total) * 70, 0.7);
              return `<button class="trace-row status-${event.status} ${state.selectedLane === "original" && event.seq === state.selectedSeq ? "selected" : ""}" type="button" data-event="${event.seq}" style="--depth:0;--bar-left:${left}%;--bar-width:${width}%">
                <span class="trace-gutter"><i>${seqLabel(event.seq)}</i><b data-fork="${event.seq}">fork here</b></span>
                <span class="trace-event"><em>${escapeHtml(event.kind)}</em><strong>${typeof data.forkSeq === "number" && data.branches.length > 0 && event.seq === data.forkSeq ? `<span class="fork-badge">⑂ ${data.branches.length} fixes forked here</span>` : ""}${escapeHtml(event.label || event.name)}</strong><small>${escapeHtml(event.summary)}</small></span>
                <span class="waterfall"><i></i></span>
                <span class="trace-time">${statusWord(event.status)}</span>
              </button>`;
            }).join("") || `<p class="empty-line">No trace events match “${escapeHtml(state.filter)}”.</p>`}
          </div>
          <footer class="trace-foot"><span>j/k select · f fork · / filter</span><span>Bars show execution order</span></footer>
        </section>`;
    }

    function renderInvestigation() {
      const live = investigationLive();
      return `
        <section class="view investigation-view">
          ${viewHeading("AUTONOMOUS INVESTIGATION", "Which step caused the failure — and which fix provably removes it?", "Recorded failure → blamed step → three fixes re-run in isolated sandboxes → measured verdict.")}
          <section class="stage">
            <div class="section-heading"><div><span class="eyebrow"><span class="stage-no">01</span> THE FAILURE</span><h2>${escapeHtml(data.failure.title)}</h2></div></div>
            <p>${failureDetailHtml()}</p>
            ${resolutionPanel()}
          </section>
          <section class="stage">
            <div class="section-heading"><div><span class="eyebrow"><span class="stage-no">02</span> THE BLAME</span><h2>Causal ranking</h2></div><span class="section-note">${data.candidates.length} candidates</span></div>
            <p>The analyst reads the recorded trace — every step, tool call, and permission in force — plus the graders' evidence: each leaked value with the exact file and spreadsheet cell it came from. It ranks the steps most likely to have caused the failure; the top step becomes the save point every fix rewinds to.</p>
            <div class="candidate-list">${data.candidates.map((candidate) => `<button class="candidate-row ${candidate.rank === 1 ? "top" : ""}" data-inspect-seq="${candidate.seq}" type="button"><span>${String(candidate.rank).padStart(2, "0")}</span><span><b>${escapeHtml(candidate.label || `event ${seqLabel(candidate.seq)}`)}${candidate.rank === 1 ? `<span class="fork-point"> → fork point</span>` : ""}</b><small>${seqLabel(candidate.seq)} · ${escapeHtml(candidate.summary)}</small></span><span class="culpability"><i style="width:${Math.round(candidate.score * 100)}%"></i></span><strong>${Math.round(candidate.score * 100)}%</strong></button>`).join("") || '<p class="empty-line">Ranking begins after a failed run is investigated.</p>'}</div>
          </section>
          <section class="stage">
            <div class="section-heading"><div><span class="eyebrow"><span class="stage-no">03</span> THE EXPERIMENT</span>${live ? "<h2>Branch race</h2>" : ""}</div>${replayButton()}</div>
            ${live ? branchRace(false, { heading: false }) : forkMap()}
          </section>
          <section class="stage">
            <div class="section-heading"><div><span class="eyebrow"><span class="stage-no">04</span> THE VERDICT</span></div></div>
            ${verdictBlock()}
          </section>
        </section>`;
    }

    function forkMap() {
      if (typeof data.forkSeq !== "number" || !data.branches.length) return "";
      const ROW = 26;
      const COL = 56;
      const R = 4.5;
      const TOP = 16;
      const x0 = 24;
      const shared = (data.trace || []).filter((event) => event.seq <= data.forkSeq);
      const originalDown = (data.trace || []).filter((event) => event.seq > data.forkSeq);
      const branches = data.branches;
      const sharedRows = Math.max(shared.length, 1);
      const forkRow = Math.max(shared.length - 1, 0);
      const downStart = forkRow + 1;
      const yAt = (row) => TOP + row * ROW;
      const railX = (index) => x0 + COL * (index + 1);
      const forkY = yAt(forkRow);

      const branchEvents = (branch) => (Array.isArray(branch.branchTrace) ? branch.branchTrace : [])
        .filter((event) => event.seq !== -1 && event.kind !== "more");
      const moreMark = (branch) => {
        const extra = (branch.branchTrace || []).find((event) => event.kind === "more" || event.seq === -1);
        if (!extra) return "";
        const match = String(extra.label || "").match(/\+\d+/);
        return match ? match[0] : "+";
      };
      const outcomeOf = (status) => (status === "winner" ? "winner" : status === "pass" || status === "passed" ? "pass" : "fail");
      const outcomeLabel = (kind) => (kind === "winner" ? "WINNER" : kind === "pass" ? "PASS" : "FAIL");
      const clip = (value, limit = 48) => {
        const text = String(value || "");
        return text.length <= limit ? text : `${text.slice(0, limit - 1)}…`;
      };
      const leaksFor = (event) => {
        if (!event?.effectId) return 0;
        return (data.effects || []).find((item) => item.id === event.effectId)?.leaks?.length || 0;
      };
      const railLen = (count, extra) => Math.max(count + extra, count === 0 ? 1 : count);
      const maxRailLen = Math.max(
        originalDown.length,
        ...branches.map((branch) => railLen(branchEvents(branch).length, moreMark(branch) ? 1 : 0)),
        1,
      );
      const width = 24 + COL * 4 + 320;
      const height = (sharedRows + 1 + maxRailLen + 2) * ROW + 32;
      const padLeft = 56;

      const nodeTip = (seq, label) => `<title>${seqLabel(seq)} · ${escapeHtml(label)}</title>`;
      const dot = (x, y, cls, key, seq, label, r = R) => {
        const selected = `${state.selectedLane}:${state.selectedSeq}` === key ? " selected" : "";
        const tip = nodeTip(seq, label);
        return `<circle class="hit" cx="${x}" cy="${y}" r="10" fill="transparent" data-map-node="${escapeHtml(key)}">${tip}</circle><circle class="dot ${cls}${selected}" cx="${x}" cy="${y}" r="${r}" data-map-node="${escapeHtml(key)}">${tip}</circle>`;
      };
      const term = (x, row, kind, emptyNote = "") => {
        const y = yAt(row);
        const note = emptyNote
          ? `<text class="empty" x="${x}" y="${y + 26}" text-anchor="middle">${escapeHtml(emptyNote)}</text>`
          : "";
        const verdict = outcomeLabel(kind);
        return `<circle class="term ${kind}" cx="${x}" cy="${y}" r="6"><title>${escapeHtml(verdict)}</title></circle><text class="term ${kind}" x="${x}" y="${y + 14}" text-anchor="middle">${verdict}</text>${note}`;
      };

      const origTermRow = downStart + originalDown.length;
      let marks = `<path class="rail original" d="M ${x0} ${yAt(0)} L ${x0} ${yAt(origTermRow)}"/>`;
      branches.forEach((branch, index) => {
        const xi = railX(index);
        const events = branchEvents(branch);
        const more = moreMark(branch);
        const lastEventRow = events.length ? downStart + events.length - 1 : null;
        const moreRow = more && events.length ? downStart + events.length : null;
        const termRow = events.length ? downStart + events.length + (more ? 1 : 0) : downStart + 1;
        const landing = yAt(downStart);
        const solidEnd = lastEventRow == null ? termRow : lastEventRow;
        marks += `<path class="rail rail-${index}" d="M ${x0} ${forkY} C ${x0} ${forkY + ROW * 0.8} ${xi} ${forkY + ROW * 0.2} ${xi} ${landing} L ${xi} ${yAt(solidEnd)}"/>`;
        if (moreRow != null) {
          marks += `<path class="rail rail-${index} dashed" d="M ${xi} ${yAt(lastEventRow)} L ${xi} ${yAt(moreRow)}"/>`;
          marks += `<text class="more" x="${xi + 8}" y="${yAt(moreRow)}" dominant-baseline="central">${escapeHtml(more)}</text>`;
          marks += `<path class="rail rail-${index}" d="M ${xi} ${yAt(moreRow)} L ${xi} ${yAt(termRow)}"/>`;
        } else if (lastEventRow != null) {
          marks += `<path class="rail rail-${index}" d="M ${xi} ${yAt(lastEventRow)} L ${xi} ${yAt(termRow)}"/>`;
        }
        marks += `<text class="letter rail-${index}" x="${xi - 8}" y="${landing}" text-anchor="end" dominant-baseline="central">${escapeHtml(branch.letter || "")}</text>`;
      });

      shared.forEach((event, index) => {
        const y = yAt(index);
        const isFork = event.seq === data.forkSeq;
        const label = `${seqLabel(event.seq)}  ${clip(event.label || event.name || "")}${isFork ? "  ⑂" : ""}`;
        marks += dot(x0, y, isFork ? "fork" : "shared", `original:${event.seq}`, event.seq, event.label || event.name || "", isFork ? 6 : R);
        marks += `<text class="shared" x="${x0 + 16}" y="${y}" dominant-baseline="central">${escapeHtml(label)}</text>`;
      });
      originalDown.forEach((event, index) => {
        const y = yAt(downStart + index);
        const key = `original:${event.seq}`;
        marks += dot(x0, y, "original", key, event.seq, event.label || event.name || "");
        const leaks = leaksFor(event);
        if (leaks) marks += `<text class="leaked" x="${x0 - 8}" y="${y}" text-anchor="end" dominant-baseline="central">${leaks} leaked</text>`;
      });
      marks += term(x0, origTermRow, "fail");

      branches.forEach((branch, index) => {
        const xi = railX(index);
        const events = branchEvents(branch);
        const more = moreMark(branch);
        const kind = outcomeOf(branch.finalStatus);
        events.forEach((event, eventIndex) => {
          marks += dot(xi, yAt(downStart + eventIndex), `rail-${index}`, `${branch.id}:${event.seq}`, event.seq, event.label || event.name || "");
        });
        const empty = events.length === 0;
        const termRow = empty ? downStart + 1 : downStart + events.length + (more ? 1 : 0);
        marks += term(xi, termRow, kind, empty ? "no persisted re-run" : "");
      });

      return `
        <section class="fork-map">
          <div class="section-heading"><div><span class="eyebrow">DIVERGENCE MAP</span><h2>One history, four futures</h2></div></div>
          <div class="map-scroll"><svg viewBox="${-padLeft} 0 ${width + padLeft} ${height}" width="${width + padLeft}" height="${height}" role="img" aria-label="Fork map">${marks}</svg></div>
        </section>`;
    }

    function renderBranches() {
      return `
        <section class="view branches-view">
          ${viewHeading(`${data.branches.length} ISOLATED CLOUD RUN SANDBOXES`, "Executed alternatives", "Culprit replaces confident reasoning with executed evidence—and contradicts you when you are wrong.", `<span class="view-metric"><b>${data.branches.filter((branch) => branch.finalStatus === "pass" || branch.finalStatus === "winner").length} / ${data.branches.length}</b> pass all criteria</span>`)}
          ${branchRace(true)}
        </section>`;
    }

    function diffLines(lines, side, leaks = []) {
      return lines.map((line) => {
        const marked = side === "removed" ? highlightLeaks(line, leaks) : escapeHtml(line);
        const changed = side === "removed"
          ? marked.includes("<mark") || /Disclosed|Source run safety/.test(line)
          : /absent|Redacted branch safety/.test(line);
        return `<div class="diff-line ${changed ? side : ""}"><span>${changed ? (side === "removed" ? "−" : "+") : " "}</span><code>${marked || " "}</code></div>`;
      }).join("");
    }

    function criteriaGrid(compact = false) {
      const columns = ["original", ...data.branches.map((branch) => branch.id)];
      const labels = ["original", ...data.branches.map((branch) => `(${branch.letter || branch.id}) ${branch.shortLabel}`)];
      return `<div class="criteria-scroll"><table class="criteria-grid ${compact ? "compact" : ""}">
        <thead><tr><th>CRITERION</th>${labels.map((label, index) => {
          const isWinner = columns[index] === winner?.id;
          return `<th class="${isWinner ? "winner-col" : ""}">${escapeHtml(label)}${isWinner ? "<small>winner</small>" : ""}</th>`;
        }).join("")}</tr></thead>
        <tbody>${data.criteria.map((criterion) => `<tr><th>${escapeHtml(criterion.label)}</th>${columns.map((column, index) => {
          const value = criterion[column];
          const valueClass = value.includes("pass") ? "pass" : value.includes("fail") ? "fail" : "metric";
          const isWinner = column === winner?.id;
          return `<td class="${valueClass} ${isWinner ? "winner-col" : ""}">${escapeHtml(value)}</td>`;
        }).join("")}</tr>`).join("")}</tbody>
      </table></div>`;
    }

    function renderOutcome() {
      const email = data.emails[state.emailIndex];
      if (!winner || !email) {
        const inv = data.investigation || {};
        const copy = inv.failClosed
          ? "Judging failed closed: no repair passed every rule, so there is no winner to diff."
          : inv.error
            ? "The investigation ended in an error before a winner could be judged."
            : "A winning branch will appear after judging completes.";
        return `<section class="view outcome-view">${viewHeading("COUNTERFACTUAL OUTCOME", "Outcome diff", copy)}${resolutionPanel() || '<p class="empty-line">No winning branch is available yet.</p>'}</section>`;
      }
      return `
        <section class="view outcome-view">
          ${viewHeading(`WINNER · FIX ${escapeHtml(data.outcome.winnerIndex.toUpperCase())}`, "Outcome diff", "The selected repair is ranked on measured criteria, capability scope, change size, cost, and duration.", `<span class="winner-stamp">${escapeHtml(data.outcome.elapsed)} · ${escapeHtml(data.outcome.cost)}</span>`)}
          <section class="diff-surface workspace-diff">
            <div class="surface-heading"><span><b>01</b> Capability delta</span><strong>${escapeHtml(data.outcome.capabilityDelta)}</strong></div>
            <div class="no-change"><code>${escapeHtml(data.outcome.changeSize)}</code><span>${escapeHtml(data.outcome.rankRationale)}</span></div>
          </section>
          <section class="diff-surface effect-diff">
            <div class="surface-heading"><span><b>02</b> Effect ledger · email.send</span><strong>2 / 2 novel effects · verified evidence</strong></div>
            <div class="email-switcher" role="tablist" aria-label="Supplier messages">
              ${data.emails.map((item, index) => `<button role="tab" aria-selected="${state.emailIndex === index}" class="${state.emailIndex === index ? "active" : ""}" data-email="${index}" type="button">${escapeHtml(item.supplier)}</button>`).join("")}
            </div>
            <div class="effect-address"><span>run</span> ${escapeHtml(email.target)}</div>
            <div class="email-compare">
              <div><header><span>FAILED ORIGINAL</span><b>${email.leaks?.length ? `${email.leaks.length} leaked values highlighted` : "verified result"}</b></header>${diffLines(email.original, "removed", email.leaks || [])}</div>
              <div><header><span>WINNER · FIX ${escapeHtml((winner.letter || winner.id).toUpperCase())}</span><b>isolated · novel</b></header>${diffLines(email.winner, "added")}</div>
            </div>
          </section>
          <section class="diff-surface criteria-diff">
            <div class="surface-heading"><span><b>03</b> Criteria grid</span><strong>same graders · all branches</strong></div>
            ${criteriaGrid(true)}
          </section>
        </section>`;
    }

    function renderEffects() {
      const filters = ["all", "original", ...data.branches.map((branch) => branch.id)];
      const shown = data.effects.filter((effect) => state.effectFilter === "all" || effect.branch === state.effectFilter);
      return `
        <section class="view effects-view">
          ${viewHeading("BROKERED OUTWARD ACTIONS", "Effect ledger", "Recorded source actions and isolated branch actions are shown from Firestore.", `<span class="view-metric"><b>${data.effects.filter((effect) => effect.novel).length}</b> novel effects</span>`)}
          <div class="effect-filters" aria-label="Filter effects">${filters.map((filter) => {
            const branch = data.branches.find((item) => item.id === filter);
            const label = filter === "all" ? "All" : filter === "original" ? "Original" : `Fix ${escapeHtml((branch?.letter || filter).toUpperCase())}`;
            return `<button type="button" data-effect-filter="${filter}" class="${state.effectFilter === filter ? "active" : ""}">${label}</button>`;
          }).join("")}</div>
          <div class="ledger-head"><span>ID / TIME</span><span>ACTION</span><span>MODE</span><span>RESULT</span></div>
          <div class="ledger">
            ${shown.map((effect) => {
              const leaks = effect.leaks || [];
              return `<details class="ledger-row" ${effect.branch === winner?.id || leaks.length ? "open" : ""}>
              <summary><span><b>${effect.id}</b><small>${effect.at}</small></span><span><b>${effect.action}</b><small>${escapeHtml(effect.target)}</small></span><span class="mode-${effect.mode}">${effect.mode}${effect.novel ? " · NOVEL" : ""}</span><span class="effect-status ${leaks.length ? "leaking" : ""}">${leaks.length ? `${effect.status} · ${leaks.length}` : effect.status}</span></summary>
              <div class="ledger-detail"><div><span>ARGUMENTS</span><pre>${highlightLeaks(effect.args, leaks)}</pre></div><div><span>BROKER RESPONSE</span><pre>${escapeHtml(effect.response)}</pre></div>${leaks.length ? `<div class="leak-summary"><span>PROTECTED VALUES DISCLOSED · ${leaks.length}</span><pre>${leaks.map((leak) => escapeHtml(`${leak.text} ← ${leak.source || "protected internal value"}`)).join("\n")}</pre></div>` : ""}</div>
            </details>`;
            }).join("")}
          </div>
          <p class="ledger-note">Novel means the branch issued a newly generated broker call. It was not copied from the original ledger.</p>
        </section>`;
    }

    function renderCriteria() {
      const callout = resolutionPanel();
      const reason = winner
        ? `<section class="winner-reason"><span>WHY FIX ${escapeHtml((winner.letter || winner.id).toUpperCase())} WINS</span><p>${escapeHtml(data.outcome.rankRationale)}</p></section>`
        : data.investigation?.failClosed
          ? `<section class="winner-reason"><span>NO WINNING REPAIR</span><p>${escapeHtml(data.investigation.evidence || "No counterfactual passed every criterion.")}</p></section>`
          : "";
      return `
        <section class="view criteria-view">
          ${viewHeading("EXECUTED EVIDENCE", "Criteria grid", "Safety and task quality are evaluated together; measured ranking breaks ties between passing repairs.", winner ? `<span class="winner-stamp">winner · fix (${escapeHtml(winner.letter || winner.id)})</span>` : "")}
          ${callout}
          ${criteriaGrid(false)}
          <div class="criteria-legend"><span><i class="pass"></i> pass</span><span><i class="fail"></i> fail</span><span><i class="winner"></i> selected winner</span></div>
          ${reason}
        </section>`;
    }

    function renderRaw() {
      const raw = JSON.stringify({ run: data.run, failure: data.failure, branches: data.branches, criteria: data.criteria, effects: data.effects, trace: data.trace }, null, 2);
      return `
        <section class="view raw-view">
          ${viewHeading("IMMUTABLE INVESTIGATION RECORD", "Raw", "The Firestore-derived snapshot used by this live interface.", `<button class="text-button" data-action="copy-raw" type="button">Copy JSON</button>`)}
          <pre id="raw-json">${escapeHtml(raw)}</pre>
        </section>`;
    }

    function renderView() {
      if (state.view === "trace") return renderTrace();
      if (state.view === "investigation") return renderInvestigation();
      if (state.view === "branches") return renderBranches();
      if (state.view === "outcome") return renderOutcome();
      if (state.view === "effects") return renderEffects();
      if (state.view === "criteria") return renderCriteria();
      return renderRaw();
    }

    function inspector() {
      const event = currentEvent();
      const effect = event.effectId ? data.effects.find((item) => item.id === event.effectId) : null;
      const branch = state.selectedLane !== "original"
        ? data.branches.find((item) => item.id === state.selectedLane)
        : null;
      const eyebrow = branch
        ? `SANDBOX STEP · FIX ${escapeHtml(branch.letter || "")}`
        : "SELECTED STEP";
      const forkFooter = branch
        ? ""
        : `<footer><button class="text-button" data-fork="${event.seq}" type="button">f · fork at ${seqLabel(event.seq)}</button></footer>`;
      return `
        <aside class="inspector ${state.inspectorOpen ? "open" : ""}" aria-label="Event inspector" aria-hidden="${!state.inspectorOpen}">
          <header><span class="eyebrow">${eyebrow}</span><button data-action="close-inspector" aria-label="Close inspector" type="button">×</button></header>
          <div class="inspector-title"><span class="seq">${seqLabel(event.seq)}</span><div><b>${escapeHtml(event.label || event.name)}</b><small>${escapeHtml(event.name)} · ${event.kind} · verified record</small></div><span class="inspector-state state-${event.status}">${event.status}</span></div>
          <dl>
            <dt>EXECUTION</dt><dd>role        ${escapeHtml(event.role)}\nmodel       ${escapeHtml(event.model)}\ntokens      ${escapeHtml(event.tokens)}\nlatency     ${escapeHtml(event.latency)}</dd>
            <dt>ARGUMENTS</dt><dd>${escapeHtml(event.args)}</dd>
            <dt>RESULT</dt><dd>${escapeHtml(event.result)}</dd>
            <dt>CAPABILITIES IN FORCE</dt><dd>${(event.capabilities || []).map(escapeHtml).join("\n")}</dd>
            ${event.causal ? `<dt>CAUSAL NOTE</dt><dd>${escapeHtml(event.causal)}</dd>` : ""}
            ${effect ? `<dt>EFFECT EMITTED · ${effect.id}</dt><dd>mode        ${effect.mode}\ntarget      ${escapeHtml(effect.target)}\nresponse    ${escapeHtml(effect.response)}</dd>` : ""}
          </dl>
          ${forkFooter}
        </aside>`;
    }

    function keymap() {
      if (!state.keymapOpen) return "";
      const keys = [["j / k", "next / previous trace event"], ["f", "fork at selected event"], ["/", "filter trace"], ["1–7", "switch view"], ["Esc", "close panel"], ["?", "toggle this key map"]];
      return `<div class="modal-backdrop" data-action="close-keymap"><section class="keymap" role="dialog" aria-modal="true" aria-labelledby="keymap-title"><header><h2 id="keymap-title">Keyboard</h2><button data-action="close-keymap" aria-label="Close key map" type="button">×</button></header>${keys.map(([key, action]) => `<div><kbd>${key}</kbd><span>${action}</span></div>`).join("")}</section></div>`;
    }

    function toast() {
      if (!state.toast) return "";
      return `<div class="toast ${state.toast.kind || ""}" role="status"><span>${escapeHtml(state.toast.message)}</span><button data-action="dismiss-toast" aria-label="Dismiss status" type="button">×</button></div>`;
    }

    function render({ preserveFocus = false } = {}) {
      const focusId = preserveFocus ? document.activeElement?.id : null;
      root.innerHTML = `
        <div class="app-shell ${state.inspectorOpen ? "" : "inspector-closed"}">
          ${runRail()}
          <main class="workspace">
            <header class="topbar">
              <div class="run-identity"><span class="mobile-mark" aria-hidden="true">C</span><span class="mono">${data.run.id}</span><strong>${escapeHtml(data.run.title)}</strong></div>
              <div class="top-actions"><span class="top-status ${escapeHtml(data.run.status)}">${escapeHtml(data.run.verdict)}</span><button class="text-button" data-action="new-run" type="button" ${state.startingRun ? "disabled" : ""}>${state.startingRun ? "Starting…" : "New run"}</button><button class="inspect-toggle" data-action="toggle-inspector" type="button">Inspect</button><button class="keymap-toggle" data-action="open-keymap" aria-label="Show keyboard shortcuts" type="button">?</button></div>
            </header>
            ${tabs()}
            ${renderView()}
          </main>
          ${inspector()}
          ${keymap()}
          ${toast()}
        </div>`;
      if (focusId) document.querySelector(`#${focusId}`)?.focus({ preventScroll: true });
    }

    function visibleTraceEvents() {
      const query = state.filter.trim().toLowerCase();
      return data.trace.filter((event) => !query || [event.name, event.summary, event.kind, event.args, event.result].join(" ").toLowerCase().includes(query));
    }

    async function forkAt(seq) {
      state.selectedSeq = seq;
      state.selectedLane = "original";
      state.toast = { kind: "running", message: `event ${seqLabel(seq)} · allocating isolated sandbox` };
      render();
      try {
        const result = await source.fork(seq);
        state.toast = { kind: "pass", message: `${result.branchId} · forked from checkpoint ${seqLabel(result.checkpoint)}` };
      } catch (error) {
        state.toast = { kind: "fail", message: `fork failed · ${error.message}` };
      }
      render();
    }

    async function deleteRun(runId) {
      try {
        await source.deleteRun(runId);
        state.toast = { kind: "pass", message: "run archived — removed from the rail" };
        render();
        window.setTimeout(() => location.reload(), 600);
      } catch (error) {
        state.toast = { kind: "fail", message: error.message };
        render();
      }
    }

    async function startRun() {
      if (state.startingRun) return;
      state.startingRun = true;
      state.toast = { kind: "running", message: "queueing a new run on Cloud Run" };
      render();
      try {
        const result = await source.startRun();
        state.toast = { kind: "pass", message: `${result.run_id} · queued, opening it now` };
        render();
        window.setTimeout(() => {
          location.href = `/?run=${encodeURIComponent(result.run_id)}`;
        }, 700);
      } catch (error) {
        state.startingRun = false;
        state.toast = { kind: "fail", message: `could not start a run · ${error.message}` };
        render();
      }
    }

    function replayRace() {
      data.branches.forEach((branch) => branchState.set(branch.id, { status: "queued", detail: "waiting for isolated Cloud Run sandbox", progress: 0 }));
      render();
      Promise.resolve(source.replay()).then((result) => {
        state.toast = { kind: "running", message: `${result.investigation_id} · autonomous investigation queued` };
        render();
      }).catch((error) => {
        state.toast = { kind: "fail", message: `replay failed · ${error.message}` };
        render();
      });
    }

    root.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const row = event.target.closest('[role="button"][data-run-id]');
      if (!row) return;
      event.preventDefault();
      row.click();
    });

    root.addEventListener("click", (event) => {
      const deleteBtn = event.target.closest("[data-delete-run]");
      const target = event.target.closest("[data-view], [data-inspect-effect-seq], [data-inspect-seq], [data-event], [data-fork], [data-email], [data-effect-filter], [data-action], [data-delete-run], [data-run-id], button, summary");
      if (deleteBtn) {
        event.stopPropagation();
        const id = deleteBtn.dataset.deleteRun;
        if (state.confirmDeleteRunId === id) {
          state.confirmDeleteRunId = null;
          deleteRun(id);
        } else {
          state.confirmDeleteRunId = id;
          render();
        }
        return;
      }
      if (state.confirmDeleteRunId) {
        state.confirmDeleteRunId = null;
        render();
      }
      const mapNode = event.target.closest("[data-map-node]");
      if (mapNode) {
        const key = mapNode.getAttribute("data-map-node") || "";
        const sep = key.indexOf(":");
        state.selectedLane = sep === -1 ? "original" : key.slice(0, sep);
        state.selectedSeq = Number(key.slice(sep + 1));
        state.inspectorOpen = true;
        render();
        return;
      }
      if (!target) return;
      const view = target.dataset.view;
      if (view) { routeToView(view); return; }
      if (target.dataset.runId && target.dataset.runId !== data.run.id) { location.href = `/?run=${encodeURIComponent(target.dataset.runId)}`; return; }
      if (target.dataset.inspectEffectSeq) { inspectInPlace(Number(target.dataset.inspectEffectSeq)); return; }
      if (target.dataset.inspectSeq) { inspectInPlace(Number(target.dataset.inspectSeq)); return; }
      if (target.dataset.event) { routeToEvent(Number(target.dataset.event)); return; }
      if (target.dataset.fork) { event.stopPropagation(); forkAt(Number(target.dataset.fork)); return; }
      if (target.dataset.email !== undefined) { state.emailIndex = Number(target.dataset.email); render(); return; }
      if (target.dataset.effectFilter) { state.effectFilter = target.dataset.effectFilter; render(); return; }
      const action = target.dataset.action;
      if (action === "replay") { replayRace(); return; }
      if (action === "new-run") { startRun(); return; }
      if (action === "open-filter") { state.view = "trace"; state.filterOpen = true; routeToView("trace"); render(); window.setTimeout(() => document.querySelector("#trace-filter")?.focus(), 0); return; }
      if (action === "close-inspector") { state.inspectorOpen = false; render(); return; }
      if (action === "toggle-inspector") { state.inspectorOpen = !state.inspectorOpen; render(); return; }
      if (action === "open-keymap") { state.keymapOpen = true; render(); return; }
      if (action === "close-keymap" && (target === event.target || target.tagName === "BUTTON")) { state.keymapOpen = false; render(); return; }
      if (action === "dismiss-toast") { state.toast = null; render(); return; }
      if (action === "copy-raw") {
        navigator.clipboard?.writeText(document.querySelector("#raw-json")?.textContent || "").then(() => {
          state.toast = { kind: "pass", message: "Investigation JSON copied" }; render();
        }).catch(() => { state.toast = { kind: "fail", message: "Copy failed · select the raw record manually" }; render(); });
      }
    });

    root.addEventListener("input", (event) => {
      if (event.target.id !== "trace-filter") return;
      const caret = event.target.selectionStart;
      state.filter = event.target.value;
      render();
      const input = document.querySelector("#trace-filter");
      input?.focus({ preventScroll: true });
      input?.setSelectionRange(caret, caret);
    });

    window.addEventListener("keydown", (event) => {
      const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || "");
      if (event.key === "Escape") {
        if (state.keymapOpen) state.keymapOpen = false;
        else if (state.filterOpen) { state.filterOpen = false; state.filter = ""; }
        else state.inspectorOpen = false;
        render();
        return;
      }
      if (typing) return;
      if (event.key === "?") { event.preventDefault(); state.keymapOpen = !state.keymapOpen; render(); return; }
      if (/^[1-7]$/.test(event.key)) { event.preventDefault(); routeToView(VIEW_NAMES[Number(event.key) - 1]); return; }
      if (event.key === "/") { event.preventDefault(); state.view = "trace"; state.filterOpen = true; routeToView("trace"); render(); window.setTimeout(() => document.querySelector("#trace-filter")?.focus(), 0); return; }
      if (event.key === "f") {
        event.preventDefault();
        if (state.selectedLane === "original") forkAt(state.selectedSeq);
        return;
      }
      if (event.key === "j" || event.key === "k") {
        event.preventDefault();
        const events = visibleTraceEvents();
        let index = events.findIndex((item) => item.seq === state.selectedSeq);
        if (index < 0) index = 0;
        index = Math.max(0, Math.min(events.length - 1, index + (event.key === "j" ? 1 : -1)));
        if (events[index]) routeToEvent(events[index].seq);
      }
    });

    window.addEventListener("hashchange", () => { parseRoute(); render(); });
    mobileViewport.addEventListener?.("change", (event) => {
      if (event.matches) state.inspectorOpen = false;
      render();
    });
    source.subscribe((event) => {
      if (event.type === "race_reset") data.branches.forEach((branch) => branchState.set(branch.id, { status: "queued", detail: "waiting for isolated Cloud Run sandbox", progress: 0 }));
      if (event.type === "branch_update") branchState.set(event.id, event);
      if (event.type === "snapshot_update" && event.snapshot?.run?.id === data.run.id) {
        Object.assign(data, event.snapshot);
        winner = data.branches.find((branch) => branch.finalStatus === "winner");
        culpritEvent = data.trace.find((item) => item.status === "culprit") || data.trace[0] || culpritEvent;
        data.branches.forEach((branch) => branchState.set(branch.id, { status: branch.liveStatus || "queued", detail: branch.liveDetail || "waiting for isolated Cloud Run sandbox", progress: branch.progress || 0 }));
        if (state.emailIndex >= data.emails.length) state.emailIndex = 0;
      }
      if (event.type === "stream_connected" && state.toast?.message?.includes("disconnected")) state.toast = null;
      if (event.type === "stream_error") state.toast = { kind: "fail", message: event.message || "Live stream disconnected" };
      render({ preserveFocus: true });
    });

    if (!parseRoute()) history.replaceState(null, "", `#/run/${data.run.id}/view/investigation`);
    render();
    if (source.autoReplay) window.setTimeout(replayRace, 420);
  }

  window.CulpritUI = { boot };
})();
