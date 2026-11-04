// Presentation consumes the provider API only. Replace SavedDevelopmentProvider
// server-side to connect live runs; gold-schema items stay unchanged.
const $ = (selector, root = document) => root.querySelector(selector);
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icons = {
  file:'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h6"/>',
  arrow:'<path d="M5 12h14m-6-6 6 6-6 6"/>',
  download:'<path d="M12 3v12m-5-5 5 5 5-5M5 16v5h14v-5"/>',
  link:'<path d="m10 13 4-4M8 15l-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m2 2 1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" transform="translate(1 0)"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  activity:'<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
  code:'<path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-14-2 18"/>'
};
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">${icons[name] || icons.file}</svg>`;
const human = text => String(text || 'unknown').replaceAll('_', ' ');
const short = id => String(id).split('.').slice(-2).join('.');
const time = seconds => {const n = Math.max(0, Math.floor(Number(seconds) || 0)); return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`;};
const badge = (text, style = '') => `<span class="badge ${style}">${escape(human(text))}</span>`;
const state = {catalog:null, agent:null, job:null, jobTimer:null, series:'TS3006', meeting:null, run:null, selected:'TS3006d', tab:'record', item:null, request:0, controller:null, error:null, loading:false, actionFilter:'all', activityFilter:'all'};
const jobActive = () => state.job && ['QUEUED','RUNNING'].includes(state.job.status);
const elapsed = seconds => `${Math.floor((seconds || 0) / 60)}m ${Math.floor((seconds || 0) % 60)}s`;
const integer = value => Number(value || 0).toLocaleString();

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error?.message || 'The meeting could not be opened.');
  return result;
}

function announce(text) { $('#status-message').textContent = text; }

function assignRun(run) {
  state.run = run;
  state.item = linkedItems().find(i => i.related_items.some(r => r.relation !== 'continues'))?.id || linkedItems()[0]?.id || null;
}

function livePanel() {
  const job = state.job;
  if (!job) return '';
  return `<section class="live-panel" id="live-progress" aria-label="Live agent run"><div class="live-heading"><strong>${jobActive() ? '<span class="spinner"></span> Live agent running' : `Live run · ${escape(human(job.status))}`}</strong>${badge(job.meeting_id,'neutral')}</div>
    <p>${jobActive() ? `Processing ${escape(job.current_meeting)} · ${escape(human(job.stage))}. ${job.completed_meetings.length} of ${job.sequence.length} meetings completed.` : escape(job.reason || 'Completed records and evidence are available.')}</p>
    <div class="sequence-steps">${job.sequence.map(mid => `<span class="${job.completed_meetings.includes(mid) ? 'done' : mid === job.current_meeting ? 'current' : ''}">${escape(mid)}${job.completed_meetings.includes(mid) ? ' ✓' : ''}</span>`).join('<span>→</span>')}</div>
    ${job.connection_error ? `<p class="muted">${escape(job.connection_error)} Reconnecting…</p>` : ''}<div class="live-panel-bottom"><span>${elapsed(job.elapsed_seconds)} elapsed ${state.run && jobActive() ? '· Displaying the saved record below while the new run executes.' : ''}</span>${jobActive() ? `<button class="button secondary" data-action="cancel-agent" ${job.cancel_requested ? 'disabled' : ''}>${job.cancel_requested ? 'Stopping…' : 'Stop run'}</button>` : job.status === 'COMPLETED' ? '<button class="text-button" data-action="view-live">View live result →</button>' : ''}</div></section>`;
}

function configureLive() {
  const agent = state.agent;
  if (!agent?.available) {
    openDrawer('Live agent setup', `<div class="inspected-record"><p>Saved acceptance records work without an API key. Live generation uses the accepted agent and your configured model account.</p><ol><li>Run the Windows preview shortcut again to install current UI dependencies.</li><li>Set <code>OPENAI_API_KEY</code> in <code>agent/.env</code> or the server environment.</li><li>Restart the preview server.</li></ol><p>Model and runtime settings must match the frozen handoff. Credentials stay on this computer.</p></div>`,'AGENT CONNECTION');
    return;
  }
  const sequence = [...state.meeting.prior_meeting_ids,state.selected];
  openDrawer(`Run agent for ${state.selected}`, `<div class="inspected-record"><p>A fresh live run will process these meetings in order:</p><div class="sequence-steps">${sequence.map(mid=>`<span>${escape(mid)}</span>`).join('<span>→</span>')}</div><p>Earlier records come from this run’s own outputs. Each meeting has a limit of 80 model attempts, 1,000,000 tokens, and 20 minutes. Processing can take several minutes per meeting.</p><p>This uses your model account. Saved acceptance records remain available while it runs.</p><div class="review-notice">Accepted profile: targeted follow-up. Full P2 checks are disabled; outputs remain drafts.</div><button class="button primary" data-action="start-agent">Start live run ${icon('arrow')}</button></div>`,'LIVE GENERATION');
}

async function startAgent() {
  $('#inspector').close();
  try {
    state.job = await api('/api/agent/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({meeting_id:state.selected})});
    const url = new URL(location.href); url.searchParams.set('job',state.job.job_id); history.replaceState(null,'',url);
    renderMain(); announce('Live agent started.'); pollJob(state.job.job_id);
  } catch(error) {openDrawer('Agent could not start',`<p class="panel-description">${escape(error.message)}</p>`,'AGENT CONNECTION');}
}

async function pollJob(id) {
  clearTimeout(state.jobTimer);
  try {
    const job = await api(`/api/agent/runs/${id}`);
    state.job = job;
    if (job.status === 'COMPLETED' && state.selected === job.meeting_id) {
      const run = await api(`/api/runs/${id}`);
      if (state.selected === job.meeting_id) assignRun(run);
    }
    if (jobActive() && $('#live-progress')) $('#live-progress').outerHTML = livePanel();
    else renderMain();
    if (jobActive()) state.jobTimer = setTimeout(()=>pollJob(id),2000);
    else announce(`Live agent ${human(job.status).toLowerCase()}.`);
  } catch(error) {
    if (state.job?.job_id === id) {
      state.job.connection_error = error.message;
      if ($('#live-progress')) $('#live-progress').outerHTML = livePanel();
      state.jobTimer = setTimeout(()=>pollJob(id),5000);
    }
  }
}

function renderLibrary() {
  if (!state.catalog) return;
  $('#series-select').innerHTML = state.catalog.series.map(series => `<option value="${series}" ${series === state.series ? 'selected' : ''}>${series}</option>`).join('');
  $('#meeting-list').innerHTML = state.catalog.meetings.filter(m => m.series === state.series).map(m => `
    <button class="meeting-button ${m.id === state.selected ? 'selected' : ''}" data-meeting="${m.id}" ${m.id === state.selected ? 'aria-current="true"' : ''}>
      <span class="meeting-icon">${icon('file')}</span><span class="meeting-button-text"><strong>${m.id}</strong><small>Meeting ${String(m.order).padStart(2,'0')}</small></span>
      ${m.has_saved_run ? '<span class="availability-dot" title="Saved record available"><span class="sr-only">Saved record available</span></span>' : '<span class="transcript-label">TXT</span>'}
    </button>`).join('');
}

async function selectMeeting(id) {
  const request = ++state.request;
  state.controller?.abort();
  const controller = new AbortController();
  state.controller = controller;
  state.selected = id; state.series = id.slice(0,-1); state.meeting = null; state.run = null;
  state.error = null; state.loading = true; state.item = null; state.actionFilter = 'all'; state.activityFilter = 'all';
  const url = new URL(location.href); url.searchParams.set('meeting', id); history.replaceState(null, '', url);
  renderLibrary(); renderMain(); announce(`Opening ${id}`);
  try {
    const meeting = await api(`/api/meetings/${encodeURIComponent(id)}`, {signal:controller.signal});
    if (request !== state.request) return;
    state.meeting = meeting;
    if (meeting.has_saved_run) {
      const run = await api('/api/runs', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({meeting_id:id}), signal:controller.signal});
      if (request !== state.request) return;
      assignRun(run);
    }
    state.loading = false; renderMain(); announce(`${id} ${state.run ? 'saved record opened' : 'transcript opened; no saved record available'}`);
  } catch (error) {
    if (request !== state.request || error.name === 'AbortError') return;
    state.error = error.message; state.loading = false; renderMain(); announce(error.message);
  }
}

const linkedItems = () => (state.run?.items || []).filter(i => i.related_items?.length);
const earlierIndex = () => Object.fromEntries((state.run?.earlier_records || []).flatMap(r => r.items.map(i => [i.id,i])));
const eventsFor = id => (state.run?.events || []).filter(e => e.record_id === id);
function personLabel(id, mid = state.selected) {
  const person = (state.run?.participants[mid] || state.meeting?.participants || []).find(p => p.id === id);
  return person ? `${person.role} · ${id}` : id;
}
function owners(item) {
  return item.owner?.length ? item.owner.map(id => `<span class="owner-name">${escape(personLabel(id, item.id.split('.')[0]))}</span>`).join('') : `<span class="muted">${item.owner_basis === 'ambiguous' ? 'Owner unclear' : 'Not stated'}</span>`;
}
function statusStyle(status) { return ['open','adopted','completed','resolved'].includes(status) ? 'green' : ['unknown','deferred','conditional'].includes(status) ? 'amber' : ''; }

function renderMain() {
  const m = state.meeting, run = state.run;
  const context = m?.prior_meeting_ids || [];
  $('#main').innerHTML = `
    <div class="page-title"><div><p class="eyebrow">MEETING WORKSPACE <span>/ ${escape(state.series)}</span></p><h1>${escape(state.selected)}<span class="title-dot">.</span></h1><p class="page-description">A meeting record, with the context behind every action.</p></div>
      <div class="title-actions"><button class="button secondary" data-action="transcript" ${!m ? 'disabled' : ''}>${icon('file')} Transcript</button>
        <button class="button secondary" data-action="configure-live" ${!m || jobActive() ? 'disabled' : ''}>${icon('activity')} ${jobActive() ? 'Agent running' : 'Run agent'}</button>
        <details class="export-menu"><summary class="button primary ${!run ? 'disabled' : ''}" ${!run ? 'aria-disabled="true"' : ''}>${icon('download')} Export <span class="chevron">⌄</span></summary><div class="export-options">${run ? `<a href="/api/runs/${run.run_id}/export?format=json" download>Structured JSON <span>.json</span></a><a href="/api/runs/${run.run_id}/export?format=markdown" download>Readable report <span>.md</span></a>` : '<span>Open a saved record to export.</span>'}</div></details></div>
    </div>
    ${m ? `<div class="meeting-meta"><span>${icon('clock')} ${time(m.duration_seconds)} transcript span</span><span>${icon('file')} ${m.dialogue_acts.length.toLocaleString()} dialogue acts</span><span class="participant-list">${m.participants.map(p => `<span class="role-chip" title="${escape(p.id)}">${escape(p.role)}</span>`).join('')}</span></div>
    <div class="context-strip"><span class="context-caption">${icon('link')} Earlier context</span><span>${context.length ? context.map(mid => `<button class="context-button" data-prior="${mid}">${mid}</button>`).join('<span class="context-arrow">→</span>') : '<span class="muted">First meeting in this series</span>'}</span><span class="context-note">${run ? `${run.earlier_records.length} saved earlier record${run.earlier_records.length === 1 ? '' : 's'}` : `${context.length} earlier transcript${context.length === 1 ? '' : 's'}`}</span></div>` : ''}
    ${livePanel()}
    ${state.loading ? `<div class="loading-page" role="status"><span class="spinner"></span>Loading ${m ? 'saved record and evidence' : 'transcript'}…</div>` : ''}
    ${state.error ? `<div class="empty-state error-state">${icon('info')}<h2>We couldn’t open this record</h2><p>${escape(state.error)}</p><button class="button secondary" data-action="retry">Try again</button></div>` : ''}
    ${run ? `<div class="run-notice"><span>${icon('check')} Schema ${escape(run.schema_version)} checked</span><span class="notice-separator"></span><span>${run.source === 'live_agent' ? 'Live agent result' : 'Saved acceptance run'}</span><span class="notice-separator"></span><span>Draft candidate · Full P2 disabled</span>${run.source === 'live_agent' ? '<button class="text-button" data-action="saved-record">Open saved acceptance</button>' : ''}</div>
      <div class="tabs" role="tablist" aria-label="Meeting record views">${[
        ['record','Meeting record',null],['changes','Cross-meeting changes',linkedItems().length],['activity','Agent activity',run.events.length],['workflow','Workflow & review',null],['report','Readable report',null],['json','JSON',null]
      ].map(([key,label,count]) => `<button id="tab-${key}" class="tab ${state.tab === key ? 'active' : ''}" role="tab" aria-selected="${state.tab === key}" aria-controls="view" tabindex="${state.tab === key ? '0' : '-1'}" data-tab="${key}">${label}${count !== null ? `<span class="tab-count">${count}</span>` : ''}</button>`).join('')}</div><section id="view" role="tabpanel" aria-labelledby="tab-${state.tab}"></section>` : ''}
    ${m && !run && !state.loading && !state.error ? `<div class="empty-state"><div class="empty-icon">${icon('file')}</div><p class="eyebrow">TRANSCRIPT READY</p><h2>This meeting is ready to review</h2><p>A saved acceptance record isn’t available for ${escape(m.id)} yet.<br>Read the transcript or use Run agent to generate a new record.</p><button class="button primary" data-action="transcript">Read transcript ${icon('arrow')}</button><button class="text-button" data-meeting="TS3006d">Open TS3006d’s saved acceptance</button></div>${previewTranscript()}` : ''}`;
  if (run) renderView();
}

function previewTranscript() {
  return `<section class="panel"><div class="panel-header"><div><p class="eyebrow">SOURCE MATERIAL</p><h2>Transcript preview</h2></div><button class="text-button" data-action="transcript">View full transcript ${icon('arrow')}</button></div><div class="preview-lines">${state.meeting.dialogue_acts.slice(0,5).map(a => `<div><span class="timestamp">${time(a.starttime)}</span><span class="role-label">${escape(a.attributes.role)}</span><p>${escape(a.text)}</p></div>`).join('')}</div></section>`;
}

function renderView() {
  const view = $('#view'); if (!view) return;
  view.innerHTML = ({record:recordView, changes:changesView, activity:activityView, workflow:workflowView, report:reportView, json:jsonView}[state.tab] || recordView)();
}

function recordView() {
  const run = state.run;
  const summaries = run.items.filter(i => i.type === 'abstract');
  const actions = run.items.filter(i => i.type === 'actions');
  const shown = state.actionFilter === 'review' ? actions.filter(i => run.unresolved_item_ids.includes(i.id)) : actions;
  return `<div class="stats-grid">${[
    ['Action items',run.counts.actions || 0,'Follow-up work','check'],['Decisions',run.counts.decisions || 0,'Recorded choices','file'],['Problems',run.counts.problems || 0,'Issues raised','info'],['Assignment flags',run.unresolved_item_ids.length,'Missing owner or ambiguous assignment','search']
  ].map(([label,count,hint,symbol],n) => `<div class="stat-card ${n === 3 ? 'review-stat' : ''}"><div><span>${label}</span><span class="stat-icon">${icon(symbol)}</span></div><strong>${count}</strong><small>${hint}</small></div>`).join('')}</div>
  <section class="panel summary-panel"><div class="panel-header"><div><p class="eyebrow">THE MEETING AT A GLANCE</p><h2>Summary</h2></div>${badge('saved abstract','neutral')}</div><ul class="summary-list">${summaries.length ? summaries.map(i => `<li><span>${escape(i.text)}</span><button class="citation-button" data-inspect="${escape(i.id)}" aria-label="Inspect evidence for summary ${short(i.id)}">${icon('link')} ${i.reference.length}</button></li>`).join('') : '<li>No summary was recorded in this saved output.</li>'}</ul></section>
  <section class="panel" id="action-items" tabindex="-1"><div class="panel-header"><div><p class="eyebrow">FROM DISCUSSION TO FOLLOW-UP</p><h2>Action items <span class="heading-count">${actions.length}</span></h2></div><div class="segmented" aria-label="Filter action items"><button data-filter="all" class="${state.actionFilter === 'all' ? 'active' : ''}" aria-pressed="${state.actionFilter === 'all'}">All actions</button><button data-filter="review" class="${state.actionFilter === 'review' ? 'active' : ''}" aria-pressed="${state.actionFilter === 'review'}">Needs review</button></div></div>
    ${shown.length ? `<div class="table-scroll"><table class="action-table"><thead><tr><th scope="col">Action</th><th scope="col">Owner</th><th scope="col">Deadline</th><th scope="col">Status</th><th scope="col"><span class="sr-only">Inspect</span></th></tr></thead><tbody>${shown.map(i => `<tr><td><span class="item-number">${escape(short(i.id))}</span><p>${escape(i.text)}</p>${i.related_items.length ? `<button class="inline-link" data-change="${escape(i.id)}">${icon('link')} ${i.related_items.length} earlier link${i.related_items.length === 1 ? '' : 's'}</button>` : ''}</td><td>${owners(i)}${badge(i.owner_basis, ['ambiguous','missing'].includes(i.owner_basis) ? 'amber' : 'subtle')}</td><td><span>${escape(i.deadline ?? 'Not stated')}</span>${badge(i.deadline_basis, i.deadline_basis === 'ambiguous' ? 'amber' : 'subtle')}</td><td>${badge(i.status,statusStyle(i.status))}</td><td><button class="inspect-button" data-inspect="${escape(i.id)}" aria-label="Inspect action ${escape(short(i.id))}">${icon('arrow')}</button></td></tr>`).join('')}</tbody></table></div>` : `<div class="section-empty">${actions.length ? 'No action items need review under this filter.' : 'No action items were recorded in this saved output.'}</div>`}
  </section>
  <div class="two-column">${['decisions','problems'].map(type => `<section class="panel"><div class="panel-header"><h2>${type === 'decisions' ? 'Decisions' : 'Problems'} <span class="heading-count">${run.counts[type] || 0}</span></h2></div>${run.items.filter(i => i.type === type).map(i => `<article class="record-item"><div>${badge(i.status,statusStyle(i.status))}<span class="item-number">${escape(short(i.id))}</span></div><p>${escape(i.text)}</p><div><button class="text-button" data-inspect="${escape(i.id)}">Inspect evidence ${icon('arrow')}</button>${i.related_items.length ? `<button class="inline-link" data-change="${escape(i.id)}">Earlier links (${i.related_items.length})</button>` : ''}</div></article>`).join('') || `<div class="section-empty">No ${type} were recorded in this saved output.</div>`}</section>`).join('')}</div>`;
}

function changesView() {
  const linked = linkedItems(), allPrior = earlierIndex();
  const selected = linked.find(i => i.id === state.item) || linked[0];
  const intro = `<div class="view-intro"><div><p class="eyebrow">FOLLOW THE PROJECT ACROSS MEETINGS</p><h2>What carried forward. What changed.</h2><p>Earlier records, the agent’s follow-up choices, and the current result.</p></div>${badge(`${linked.length} linked items`,'neutral')}</div><div class="review-notice">${icon('info')}<p>Relationships below are unreviewed predictions from the saved agent run. Inspect the retrieved material and supporting citations before accepting a change.</p></div>`;
  if (!selected) return intro + `<div class="panel empty-state"><div class="empty-icon">${icon('link')}</div><h2>No earlier item links recorded</h2><p>${state.run.earlier_records.length ? 'Earlier records are available, but this saved run did not link any current items to them.' : 'This is the first meeting. There are no earlier records in the series.'}</p>${state.run.earlier_records.map(r => `<button class="button secondary" data-prior="${r.meeting_id}">Read ${r.meeting_id} record</button>`).join('')}</div>`;
  const events = eventsFor(selected.id);
  const retrieved = events.filter(e => e.observations?.length);
  return intro + `<div class="change-layout"><nav class="change-list" aria-label="Items linked across meetings"><p class="eyebrow">LINKED ITEMS</p>${linked.map(i => `<button class="change-item ${i.id === selected.id ? 'active' : ''}" data-change="${escape(i.id)}" aria-current="${i.id === selected.id}"><span class="change-item-top"><span>${escape(short(i.id))} · ${escape(i.type)}</span>${icon('arrow')}</span><strong>${escape(i.text)}</strong><span>${[...new Set(i.related_items.map(r => r.relation))].map(r => badge(r,r === 'continues' ? 'subtle' : 'green')).join('')}</span></button>`).join('')}</nav>
  <div class="change-detail"><section class="panel"><div class="panel-header"><div><p class="eyebrow">01 · EARLIER → CURRENT</p><h2>Record relationship</h2></div>${badge('agent reported','amber')}</div>
    ${selected.related_items.map(rel => {const earlier = allPrior[rel.id]; return `<div class="relationship-pair"><article class="before-card"><div class="record-card-label"><span>EARLIER RECORD</span><button class="inline-link" data-prior="${escape(rel.id.split('.')[0])}">${escape(rel.id.split('.')[0])}</button></div><p>${escape(earlier?.text || 'Earlier record unavailable.')}</p><div>${earlier ? badge(earlier.status,statusStyle(earlier.status)) : ''}<code>${escape(rel.id)}</code></div>${earlier?.owner !== undefined ? `<div class="earlier-owner">${owners(earlier)}</div>` : ''}</article><div class="relationship-label">${icon('arrow')}${badge(rel.relation,'green')}</div><article class="after-card"><div class="record-card-label"><span>CURRENT RECORD</span><span>${escape(state.selected)}</span></div><p>${escape(selected.text)}</p><div>${badge(selected.status,statusStyle(selected.status))}<code>${escape(selected.id)}</code></div>${selected.owner !== undefined ? `<div class="earlier-owner">${owners(selected)}</div>` : ''}</article></div>`;}).join('')}
    <div class="panel-bottom"><span>Earlier records retain their original historical state.</span><button class="text-button" data-inspect="${escape(selected.id)}">Inspect current item ${icon('arrow')}</button></div></section>
    <section class="panel"><div class="panel-header"><div><p class="eyebrow">02 · THE AGENT’S FOLLOW-UP CHOICE</p><h2>Investigation trail <span class="heading-count">${events.length}</span></h2></div></div>${events.length ? timeline(events) : '<div class="section-empty">No follow-up steps were logged for this item.</div>'}</section>
    <section class="panel"><div class="panel-header"><div><p class="eyebrow">03 · WHAT THE AGENT RETRIEVED</p><h2>Retrieved material</h2></div></div><p class="panel-description">Actual delivered tool results, in their logged order. These are separate from the final supporting citations.</p>${retrieved.length ? retrieved.map(e => `<details class="retrieval-group"><summary>Step ${e.step} · ${escape(human(e.action))}<span>${e.observations.length} delivered result${e.observations.length === 1 ? '' : 's'}</span></summary><div>${e.observations.map(observationCard).join('')}</div></details>`).join('') : '<div class="section-empty">No delivered tool results were recorded for this item.</div>'}</section>
    <section class="panel"><div class="panel-header"><div><p class="eyebrow">04 · SUPPORT FOR THE CURRENT RESULT</p><h2>Final supporting citations</h2></div></div>${referenceList(selected)}<div class="panel-bottom"><span>Retrieval and final citations serve different purposes.</span><button class="text-button" data-action="action-table">View resulting action table ${icon('arrow')}</button></div></section>
  </div></div>`;
}

function timeline(events) {
  return `<ol class="timeline">${events.map(event => `<li><span class="step-number">${event.step ?? '–'}</span><div class="step-content"><div class="step-heading"><strong>${escape(human(event.action || event.outcome))}</strong>${badge(event.outcome || 'recorded', event.outcome === 'finished' ? 'green' : event.outcome === 'ok' ? 'subtle' : 'amber')}</div>${event.flags?.length ? `<div class="flag-list">${event.flags.map(flag => `<span>${escape(human(flag).toLowerCase())}</span>`).join('')}</div>` : ''}${event.args?.query ? `<p class="query">“${escape(event.args.query)}”</p>` : ''}<div class="step-meta">${event.args?.scope ? `<span>Scope: ${escape(event.args.scope)}</span>` : ''}${event.budget_left ? `<span>Lookup budget before step: ${event.budget_left.item} / item · ${event.budget_left.meeting} / meeting</span>` : ''}${event.new_info !== undefined ? `<span>${event.new_info ? 'New result IDs found' : 'No new result IDs'}</span>` : ''}</div><details class="tool-arguments"><summary>Tool arguments${event.error ? ' and error' : ''}</summary><pre>${escape(JSON.stringify(event.args || {},null,2))}</pre>${event.error ? `<p>${escape(event.error)}</p>` : ''}</details></div></li>`).join('')}</ol>`;
}

function observationCard(obs) {
  return `<article class="observation"><div><span class="source-label">${escape(obs.meeting_id || (obs.kind === 'document' ? 'Project document' : obs.kind))}</span>${obs.role ? badge(obs.role,'subtle') : ''}<code>${escape(obs.locator)}</code></div><p>${escape(obs.text)}</p>${obs.kind === 'record' ? `<div>${badge(obs.item.status,statusStyle(obs.item.status))}${obs.item.owner !== undefined ? owners(obs.item) : ''}</div>` : ''}</article>`;
}

function referenceList(item) {
  return `<div class="reference-list">${item.reference.map((ref,n) => {const evidence = state.run.evidence[ref.source_type + ':' + ref.locator];return `<button class="reference-row" data-evidence-item="${escape(item.id)}" data-reference="${n}"><span class="reference-icon">${icon(ref.source_type === 'dialogue' ? 'file' : 'info')}</span><span><span class="reference-kind">${escape(human(ref.source_type))} ${evidence?.meeting_id ? `· ${escape(evidence.meeting_id)}` : ''}</span><code>${escape(ref.locator)}</code><span class="reference-excerpt">${escape(evidence?.text || 'Source unavailable')}</span></span>${icon('arrow')}</button>`;}).join('') || '<div class="section-empty">No supporting citations were recorded.</div>'}</div>`;
}

function activityView() {
  const items = state.run.items.filter(i => i.type !== 'abstract' && eventsFor(i.id).length);
  const events = state.activityFilter === 'all' ? state.run.events : eventsFor(state.activityFilter);
  return `<div class="view-intro"><div><p class="eyebrow">OBSERVABLE AGENT ACTIVITY</p><h2>From a flag to a follow-up choice.</h2><p>Actual tool choices and outcomes from this saved meeting run.</p></div><label class="activity-select">Filter by item<select id="activity-filter"><option value="all">All items (${state.run.events.length} steps)</option>${items.map(i => `<option value="${escape(i.id)}" ${state.activityFilter === i.id ? 'selected' : ''}>${escape(short(i.id))} · ${escape(i.text.slice(0,75))}</option>`).join('')}</select></label></div><section class="panel">${events.length ? timeline(events) : '<div class="section-empty">No follow-up agent activity was logged.</div>'}</section>`;
}

function workflowView() {
  const w = state.run.workflow;
  if (!w) return '<div class="section-empty">Workflow metadata is unavailable.</div>';
  const budget = w.budget || {}, usage = w.usage || {};
  const stages = w.stages.filter(e => e.event === 'stage_started' || e.event === 'completed' || e.event === 'finished');
  return `<div class="view-intro"><div><p class="eyebrow">THE CONNECTED AGENT WORKFLOW</p><h2>Run provenance & review status.</h2><p>Actual metadata and stage events from the same run as this meeting record.</p></div>${badge(state.run.source === 'live_agent' ? 'Live result' : 'Recorded acceptance','green')}</div>
    <div class="review-notice">${icon('info')}<p><strong>Draft candidate.</strong> This accepted release uses targeted follow-up. Full P2 support, conflict, relation-witness and document checks are disabled. A completed run is not a claim that every item is correct.</p></div>
    <div class="stats-grid"><div class="stat-card"><div>Elapsed time</div><strong>${elapsed(w.elapsed_seconds)}</strong><small>Actual meeting runtime</small></div><div class="stat-card"><div>Model attempts</div><strong>${integer(budget.attempted_calls)}</strong><small>Limit ${integer(budget.limits?.calls)} / meeting</small></div><div class="stat-card"><div>Charged tokens</div><strong>${integer(budget.charged_tokens)}</strong><small>Includes retries and unknown usage charges</small></div><div class="stat-card"><div>Full P2 checks</div><strong>${w.p2_enabled ? 'On' : 'Off'}</strong><small>Effective policy, not a requested setting</small></div></div>
    <section class="panel"><div class="panel-header"><h2>Release identity</h2></div><dl class="release-fields"><div><dt>Profile</dt><dd>${escape(w.profile)}</dd></div><div><dt>Runtime reference</dt><dd><code>${escape(w.reference)}</code></dd></div><div><dt>Model</dt><dd>${escape(w.model)}</dd></div><div><dt>Agent run ID</dt><dd><code>${escape(w.agent_run_id)}</code></dd></div><div><dt>Input / output tokens</dt><dd>${integer(usage.in)} / ${integer(usage.out)}</dd></div></dl></section>
    <section class="panel"><div class="panel-header"><h2>Human follow-up</h2></div>${w.clarifications.length ? w.clarifications.map(q=>`<article class="record-item"><div>${badge(q.field,'amber')}<code>${escape(q.record_id || q.item_key)}</code></div><p>${escape(q.question)}</p><small>${escape(q.reason)}</small></article>`).join('') : `<p class="panel-description">${w.p2_enabled ? 'No formal clarification requests were recorded.' : 'Formal P2 clarification requests were not generated because full P2 is disabled. Missing or ambiguous fields in the action table still need human review.'}</p>`}</section>
    <section class="panel"><div class="panel-header"><h2>Recorded stage transitions</h2></div><ol class="stage-list">${stages.map(e=>`<li><span>${escape(human(e.current_stage || e.stage))}</span><span>${escape(human(e.event))}</span><code>#${e.seq}</code></li>`).join('')}</ol></section>`;
}

function reportView() {
  return `<div class="view-intro"><div><p class="eyebrow">READABLE FOLLOW-UP ARTIFACT</p><h2>The full meeting report.</h2><p>Rendered from the saved Markdown report belonging to this record.</p></div><a class="button secondary" href="/api/runs/${state.run.run_id}/export?format=markdown" download>${icon('download')} Markdown</a></div><article class="panel markdown-report">${state.run.report_html}</article>`;
}

function jsonView() {
  return `<div class="view-intro"><div><p class="eyebrow">STRUCTURED OUTPUT</p><h2>Gold schema. Original records.</h2><p>The validated item array is exported without UI metadata or added fields.</p></div><a class="button secondary" href="/api/runs/${state.run.run_id}/export?format=json" download>${icon('download')} JSON</a></div><div class="json-toolbar"><span>${icon('check')} Schema ${escape(state.run.schema_version)} · ${state.run.items.length} records</span></div><pre class="json-code">${escape(JSON.stringify(state.run.items,null,2))}</pre>`;
}

function openDrawer(title, body, eyebrow = 'SOURCE DETAIL') {
  $('#inspector-title').textContent = title;
  $('#inspector-eyebrow').textContent = eyebrow;
  $('#inspector-body').innerHTML = body;
  if (!$('#inspector').open) $('#inspector').showModal();
  $('#inspector-body').scrollTop = 0;
}

function inspectItem(id) {
  const item = state.run?.items.find(i => i.id === id); if (!item) return;
  openDrawer('Record & supporting evidence', `<div class="inspected-record"><div>${badge(item.type,'neutral')}${item.status ? badge(item.status,statusStyle(item.status)) : ''}</div><p>${escape(item.text)}</p><code>${escape(item.id)}</code>${item.type === 'actions' ? `<dl class="field-list"><div><dt>Owner</dt><dd>${owners(item)}${badge(item.owner_basis,'subtle')}</dd></div><div><dt>Deadline</dt><dd>${escape(item.deadline ?? 'Not stated')} ${badge(item.deadline_basis,'subtle')}</dd></div></dl>` : ''}${item.type !== 'abstract' ? `<p class="context-detail">Prior context needed: <strong>${item.prior_context_needed === null ? 'Unreviewed' : item.prior_context_needed ? 'Yes' : 'No'}</strong></p>` : ''}</div><h3 class="drawer-section-title">Supporting citations (${item.reference.length})</h3>${referenceList(item)}${eventsFor(item.id).length ? `<div class="drawer-action"><button class="button secondary" data-item-activity="${escape(id)}">${icon('activity')} Inspect agent choices</button></div>` : ''}`,'RECORD DETAIL');
}

function inspectEvidence(itemId, index) {
  const item = state.run?.items.find(i => i.id === itemId); if (!item) return;
  const ref = item.reference[index]; if (!ref) return;
  const evidence = state.run.evidence[ref.source_type + ':' + ref.locator];
  openDrawer('Source evidence', `<button class="text-button back-link" data-inspect="${escape(itemId)}">← Back to record</button><div class="evidence-caption">${badge(human(ref.source_type),'neutral')}<code>${escape(ref.locator)}</code></div><p class="source-path">${escape(ref.source)}</p>${evidence?.context ? `<p class="context-explanation">The cited segment is highlighted. Surrounding dialogue is included for context.</p><div class="dialogue-context">${evidence.context.map(a => `<article class="dialogue-line ${a.highlight ? 'highlighted' : ''}"><div><span class="timestamp">${time(a.starttime)}</span>${badge(a.role,'subtle')}<span class="participant-id">${escape(a.participant)}</span>${a.highlight ? '<span class="cited-label">CITED</span>' : ''}</div><p>${escape(a.text)}</p><code>${escape(a.id)}</code></article>`).join('')}</div>` : `<div class="source-text">${escape(evidence?.text || 'This source is unavailable in the saved run.')}</div>`}`,'SUPPORTING CITATION');
}

async function inspectPrior(mid) {
  if (!state.meeting?.prior_meeting_ids.includes(mid)) return;
  const earlier = state.run?.earlier_records.find(r => r.meeting_id === mid);
  const request = state.request;
  openDrawer(`${mid} · Earlier record`, '<p class="panel-description">Loading earlier context…</p>','EARLIER MEETING');
  if (earlier) {
    openDrawer(`${mid} · Earlier record`, `<div class="drawer-notice">Saved agent output · Human review pending</div>${earlier.items.filter(i => i.type !== 'abstract').map(i => `<article class="record-item"><div>${badge(i.type,'neutral')}${badge(i.status,statusStyle(i.status))}</div><p>${escape(i.text)}</p><code>${escape(i.id)}</code>${i.type === 'actions' ? `<div class="earlier-owner">${owners(i)}<span>Deadline: ${escape(i.deadline ?? 'Not stated')}</span></div>` : ''}</article>`).join('')}`,'EARLIER MEETING');
  } else {
    try {
      const meeting = await api(`/api/meetings/${mid}`);
      if (request !== state.request || !$('#inspector').open) return;
      showTranscript(meeting, true);
    } catch(error) { if (request === state.request && $('#inspector').open) openDrawer('Earlier context unavailable', `<p class="panel-description">${escape(error.message)}</p>`); }
  }
}

function showTranscript(meeting = state.meeting, prior = false) {
  if (!meeting) return;
  openDrawer(`${meeting.id} · Transcript`, `<div class="transcript-metadata"><span>${meeting.dialogue_acts.length} dialogue acts</span><span>${time(meeting.duration_seconds)} span</span></div>${meeting.parser_warnings.length ? `<div class="drawer-notice">${escape(meeting.parser_warnings.join(' '))}</div>` : ''}<label class="transcript-search">${icon('search')}<input id="transcript-search" type="search" placeholder="Search this transcript" aria-label="Search this transcript"></label><p id="transcript-match-count" class="match-count" aria-live="polite">${meeting.dialogue_acts.length} segments</p><div id="transcript-lines">${meeting.dialogue_acts.map(a => `<article class="dialogue-line" data-search-text="${escape(a.text.toLowerCase())}"><div><span class="timestamp">${time(a.starttime)}</span>${badge(a.attributes.role,'subtle')}<span class="participant-id">${escape(a.attributes.participant)}</span></div><p>${escape(a.text)}</p><code>${escape(a.id)}</code></article>`).join('')}</div>`,prior ? 'EARLIER TRANSCRIPT · NO SAVED RECORD' : 'SOURCE TRANSCRIPT');
}

document.addEventListener('click', event => {
  const button = event.target.closest('button'); if (!button || button.disabled) return;
  if (button.dataset.meeting) { $('#inspector').close(); selectMeeting(button.dataset.meeting); }
  else if (button.dataset.tab) {
    state.tab = button.dataset.tab; renderMain(); $(`#tab-${state.tab}`)?.focus();
  } else if (button.dataset.action === 'transcript') showTranscript();
  else if (button.dataset.action === 'configure-live') configureLive();
  else if (button.dataset.action === 'start-agent') startAgent();
  else if (button.dataset.action === 'saved-record') selectMeeting(state.selected);
  else if (button.dataset.action === 'view-live') showLiveResult();
  else if (button.dataset.action === 'cancel-agent') cancelAgent();
  else if (button.dataset.action === 'action-table') {state.tab = 'record'; state.actionFilter = 'all'; renderMain(); $('#action-items')?.focus();}
  else if (button.dataset.action === 'retry') selectMeeting(state.selected);
  else if (button.dataset.filter) {state.actionFilter = button.dataset.filter; renderView();}
  else if (button.dataset.change) {state.item = button.dataset.change; state.tab = 'changes'; renderMain();}
  else if (button.dataset.inspect) inspectItem(button.dataset.inspect);
  else if (button.dataset.evidenceItem) inspectEvidence(button.dataset.evidenceItem, Number(button.dataset.reference));
  else if (button.dataset.prior) inspectPrior(button.dataset.prior);
  else if (button.dataset.itemActivity) {state.activityFilter = button.dataset.itemActivity; state.tab = 'activity'; $('#inspector').close(); renderMain(); $(`#tab-activity`)?.focus();}
});

$('#series-select').addEventListener('change', event => selectMeeting(event.target.value + 'a'));
$('#close-inspector').addEventListener('click', () => $('#inspector').close());
$('#inspector').addEventListener('click', event => {if (event.target === $('#inspector')) $('#inspector').close();});
document.addEventListener('change', event => {if (event.target.id === 'activity-filter') {state.activityFilter = event.target.value; renderView();}});
document.addEventListener('input', event => {
  if (event.target.id !== 'transcript-search') return;
  const query = event.target.value.toLowerCase().trim(); let matches = 0;
  document.querySelectorAll('#transcript-lines .dialogue-line').forEach(line => {line.hidden = !line.dataset.searchText.includes(query); if (!line.hidden) matches++;});
  $('#transcript-match-count').textContent = `${matches} matching segments`;
});
document.addEventListener('keydown', event => {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.target.getAttribute('role') !== 'tab' || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
  const tabs = [...document.querySelectorAll('[role="tab"]')]; const current = tabs.indexOf(event.target);
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
  event.preventDefault(); tabs[next].click();
});

async function init() {
  try {
    [state.catalog,state.agent] = await Promise.all([api('/api/catalog'),api('/api/agent')]);
    const requested = new URL(location.href).searchParams.get('meeting');
    const initial = state.catalog.meetings.some(m => m.id === requested) ? requested : 'TS3006d';
    await selectMeeting(initial);
    const job = new URL(location.href).searchParams.get('job');
    if (/^[a-f0-9]{32}$/.test(job || '')) pollJob(job);
  } catch(error) {
    $('#main').innerHTML = `<div class="empty-state error-state"><h1>The meeting library is unavailable</h1><p>${escape(error.message)}</p><a class="button secondary" href="/">Reload library</a></div>`;
  }
}

async function showLiveResult() {
  const job = state.job;
  if (job?.status !== 'COMPLETED') return;
  try {
    if (state.selected !== job.meeting_id) await selectMeeting(job.meeting_id);
    const run = await api(`/api/runs/${job.job_id}`);
    if (state.selected === job.meeting_id) {assignRun(run); renderMain();}
  } catch(error) {openDrawer('Live result unavailable',`<p class="panel-description">${escape(error.message)}</p>`);}
}

async function cancelAgent() {
  if (!jobActive()) return;
  try {
    state.job = await api(`/api/agent/runs/${state.job.job_id}/cancel`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    if ($('#live-progress')) $('#live-progress').outerHTML = livePanel();
  } catch(error) {announce(error.message);}
}
init();
