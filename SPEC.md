# Demo Reel implementation contract — converged AB

**Goal:** Installable public Hermes plugin by **Nov 6 EOD PT**, usable by entrants Nov 7; polished v1 Nov 9; submission cutoff Nov 11 1PM PT. Architecture only on this card; nothing here claims the plugin is implemented.
**Architecture:** A persisted, approval-bound interview produces a typed storyboard. A read-only snapshot supplies evidence and deterministic motion art; host-dispatched media tools and ffmpeg produce an MP4 returned to the originating chat.
**Stack:** Python 3.11+, stdlib SQLite/dataclasses, Pillow; ffmpeg/ffprobe executables. No Node/Remotion dependency for v0. Remotion is an alternative future compositor, not a second required stack.

## Scope and product gate
Personal motivation: eliminate bespoke video editing for our plugin entries. Problem: entrants need short truthful demos; solution: session-grounded narrated videos with data-driven visuals; revenue: free/open-source v0, no billing integration. First users: Hermes hackathon developers in Nous Discord, already using Hermes and X, searching session-to-demo and plugin demo video. PVP: our real Hive session becomes a shareable narrated MP4. Distribution: direct GitHub install before catalog merge; user feedback in repo issues. Critical hypothesis: historical session evidence makes useful demos without manual video editing. Failure condition: no independently playable, truthful real-session reel by Nov 6 EOD; report slip instead of declaring an installable v0.

## Requirements and phase gates
| ID | Requirement | Owner | Assertion |
|---|---|---|---|
| R1 | One or more explicitly selected read-only sessions | A | SQLite mode=ro, selected IDs only, source fingerprint and row references |
| R2 | Natural-language audience/length/aspect/tone/voice interview | A | Persistent restart/edit/cancel and missing-answer tests |
| R3 | Storyboard approve/edit before rendering | A | Revision digest approval; edits revoke approval |
| R4 | Captions, accelerated event timeline, session-data generative art | B/C | Decoded frames and counterfactual event/frame comparison |
| R5 | Host-owned TTS; audio on real release probe | C | Dispatch trace, decoded non-silent narration track |
| R6 | Generated title card through image_generate | C | Actual successful dispatch; no silent static substitute in full probe |
| R7 | Screenshot of a user-approved public URL touched by session | C | v1 capability, required in full-capabilities probe; privacy/SSRF tests |
| R8 | Optional music/sound | C | Off by default; missing provider never bypasses permissions |
| R9 | MP4 ≤90s and X caption including @NousResearch; same-chat delivery | C/D | ffprobe, decode, actual adapter media extraction |
| R10 | Rule 12: unattended missing input/approval fails cleanly | A/D | No fabricated consent or wait loop; zero external dispatch |
| R11 | GitHub install, validate, doctor, disclosures | D | Disposable-home pinned install and public immutable release |

v0 requires R1–R6,R9–R11; screenshots/music/templates remain v1 per David's explicit phasing. Full non-negotiable probe additionally requires R7. Missing a required tool is a blocked render, not a fake success. Voice-off is supported intentionally; release acceptance uses voice-on. No auto-posting to X, catalog PR or submission form.

## Verified host boundary (not invented plugin-local API)
Sources: https://hermes-agent.nousresearch.com/docs/developer-guide/plugins ; https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks ; public `hermes_cli/plugins.py` source retrieved Nov 4. `ctx.register_command(name, handler, description='', args_hint='', argument_mode=None)` accepts sync/async handlers. `ctx.dispatch_tool(tool_name: str, args: dict, **kwargs) -> str` returns tool JSON. `ctx.register_cli_command(name, help_text, setup_fn, handler_fn)` follows receipts reference. `plugin_db('demo-reel')` / `plugin_data_dir('demo-reel')` from `plugins.plugin_storage` own profile-local durable data. `ctx.llm` is the sole LLM boundary, with host consent; no direct vendor SDK credentials.

Reference: receipts public commit `89123a5c18bf8f25cd75f146e73971d9965ee86e`; vault commit `c5a9b77748e4c0cfa5d89e71ba1795025950568b`, file `03-Knowledge/Hermes/receipt-plugin-api-gotchas.md`. Gotchas: state.db joins assistant tool_calls to tool results by call ID; historical durations unknown; session usage is not turn usage. CLI wiring needs pinned-install testing beyond doctor. MEDIA extractor is a real integration seam. Desktop SDK pane only; no private DOM patch. No live core/config writes.

Manifest target: `name: demo-reel`, `version: 0.1.0`, `requires_hermes: '>=0.21.7'`, `python_dependencies: ['Pillow>=10,<13']`; register command `demo`, CLI `demo`, tool `demo_reply`. No manifest capabilities field was established by the inspected host parser; do not invent one. `ctx.llm` uses the host-owned PluginLlm facade gated by `plugins.entries.<plugin_id>.llm.*`; README documents the pinned host's user opt-in, never writes it. Registered surfaces are command, tool and CLI; media/terminal calls retain host permissions. No guessed capability strings, no required provider env vars, no secret reading and no hook that intercepts unrelated chats. Desktop uses command/media output; optional pane is SDK-only and deferred.

## Narrative-default amendment (integration D, requested by David/Critic)

Schema ownership is D's (below). Preserve dataclass shapes and canonical digests: legacy schema_version=1 snapshots/boards retain their interpretation; new snapshots/boards use schema_version=2. In v2 `safe_summary` is canonical JSON containing a bounded, redacted, event-indexed excerpt catalog, not arbitrary unreviewed prose. Capture only the selected session's user ask, recorded write/patch inputs and tool outputs; no filesystem crawling or rereading artifact paths. Label inputs as inputs, outputs as outputs; a successful invocation does not prove deployment. Source consent authorizes local capture; the exact excerpt catalog is in generation_disclosure before LLM consent. Redaction is best effort, NOT a privacy guarantee: require operator preview/canary scan and exact-board review. No provider dispatch before these approvals.

V2 LLM output: 5–6 scenes, ask → 3–4 work moments → result. Each scene cites excerpt event IDs and chooses an artifact event ID; free-form bounded script requires operator factual review and is never labelled machine-proven. A result may report only what source evidence shows, never infer deployment or invented metrics. Fail closed with INSUFFICIENT_NARRATIVE_EVIDENCE when the source lacks an ask or three tool-artifact moments. No generic log fallback. Store artifact choice as the first scene.event_ids entry; the remaining entries cite supporting excerpts. Keep existing Scene fields and at most six TTS calls. Caption segments are computed deterministically from approved scene.caption into 2–4s beats, not six long static captions. Generated title imagery is a low-opacity background only; every foreground shot contains actual frozen source excerpts, a large centered hook/caption, and the end card contains outcome + Demo Reel + public URL. The artifact panel labels recorded input/output and must not claim live screenshot/deployment provenance. Abstract art remains a background.

New internal prototypes (same signatures exposed by A–D):
```python
# narrative.py, pure local helpers; no network or file reads
redact_excerpt(text: str) -> str  # bounded best-effort removal of paths, emails, credential lines, directives
capture_excerpt(event: Event, text: str, kind: str) -> dict  # provenance ID + typed redacted excerpt + digest
excerpt_catalog(snapshot: SessionSnapshot) -> list[dict]  # parse/hash/check v2 safe_summary, bounded IDs and kinds
validate_narrative(board: Storyboard, snapshot: SessionSnapshot) -> None  # act order, cited artifacts, bounded script and cadence
caption_beats(scene: Scene) -> list[tuple[int,int,str]]  # deterministic approved-word partitions at 2–4s
```

## Frozen wire contracts (schema_version = 1; v2 amendment above)
The adjacent `schemas.py` is the concrete shared declaration skeleton published before lanes. It contains only frozen dataclasses and exception declarations, no runtime implementation. JSON serialization is canonical UTF-8/sorted keys/compact/no NaN, tuples→arrays, Paths→resolved strings. Validate externally; a dataclass constructor alone does not validate. `Job.job_version` is CAS-only; `Storyboard.board_revision` is approval-only. SourceRef contains explicit DB path/session IDs (1–3); no inferred topology or latest selector. Evidence persists privately as immutable redacted SessionSnapshot; source_sha256 fingerprints selected rows, not changing DB bytes. Every downstream stage consumes stored evidence, never rereads live source. Asset video/frame kinds have required MIME/dimension/hash metadata; speech carries scene_id. RenderSnapshot freezes evidence, approved board, generated assets and art settings before composition; its digest excludes its own digest field. RenderBundle returns exactly three PNG frame assets and one video asset. Paths and private evidence never go in public fixtures. Schema ownership transfers to integration D; lanes import, never fork declarations.

DemoOptions: duration 15..90, aspect 9:16 or 16:9; all required interview fields answered explicitly (False is a valid voice answer). Scene intervals contiguous from zero; ≤6 scenes and total <= requested duration. X caption ≤280 codepoints including @NousResearch (no auto-post). GenerationConsent hashes disclosure and selected scope before LLM; Approval additionally binds exact board digest/revision, disclosure and allowed tools/URLs. DeliveryReceipt requires actual transport acknowledgement, not MEDIA extraction alone.

## Prototype registry — all functions synchronous unless explicitly async
Plugin-local signatures below are normative; host `ctx` is treated as `Any`. Paths must resolve within the job directory; untrusted text never becomes code, shell interpolation, URL navigation or consent.

### A: session reader, interview, store, storyboard
```python
# session_reader.py
list_sessions(db_path: Path, *, limit: int = 20) -> list[dict]  # metadata only, explicit source DB, no profile crawling
read_session(db_path: Path, session_ids: tuple[str, ...], *, max_events: int = 2000) -> SessionSnapshot  # one mode=ro transaction, schema introspection, redact before leaving module
redact_event(row: dict) -> Event  # safe labels, unknown durations/status stay unknown
# store.py
open_store() -> sqlite3.Connection  # plugin_db('demo-reel'), schema migration, no source writes
load_job(owner: Owner, job_id: str) -> Job  # owner-conversation bound lookup
save_job(job: Job, *, expected_job_version: int) -> Job  # atomic compare-and-swap, increment job_version or Conflict
# interview.py
start_interview(owner: Owner, *, now: datetime) -> Job  # persist draft, 30-minute TTL
advance_interview(job: Job, answer: str, *, now: datetime) -> CommandReply  # bounded single-step NL input, no waiting loop
handle_demo(ctx: Any, raw_args: str, *, session_id: str = '', **kwargs) -> str  # slash callback; /demo <answer>, approve <digest>, edit..., resume <id>, cancel
handle_reply(ctx: Any, args: dict, **kwargs) -> str  # demo_reply tool JSON wrapper for natural follow-up, trusted owner from host only
expire_job(job: Job, *, now: datetime) -> Job  # WAITING_INPUT_EXPIRED; never synthesize approval
validate_interview(draft: InterviewDraft) -> tuple[str,...]  # names of missing fields; persist draft after every answer
record_generation_consent(job: Job, *, owner: Owner, disclosure: str, scope_sha256: str, now: datetime) -> Job  # trusted affirmative action before ctx.llm
persist_snapshot(owner: Owner, snapshot: SessionSnapshot) -> str  # immutable private evidence snapshot, returns snapshot_id
load_snapshot(owner: Owner, snapshot_id: str) -> SessionSnapshot  # owner-scoped hash-checked evidence
# storyboard.py
build_storyboard(ctx: Any, snapshot: SessionSnapshot, options: DemoOptions, consent: GenerationConsent, *, job_id: str) -> Storyboard  # ctx.llm draft from redacted evidence, validate structured output
validate_storyboard(board: Storyboard, snapshot: SessionSnapshot) -> None  # reject unsupported claims, unknown event refs, interval/duration violations
edit_storyboard(board: Storyboard, edits: tuple[Edit,...]) -> Storyboard  # bump revision/digest; invalidate approval at job boundary
approve_storyboard(job: Job, *, owner: Owner, digest: str, disclosure_sha256: str, allowed_external_tools: tuple[str,...], approved_urls: tuple[str,...], now: datetime) -> Job  # exact revision + owner, required disclosure consent
```
Rule 12: "never route around approvals. Fail cleanly when unattended." Trusted owner comes only from authenticated host context; if profile, user or destination cannot be established, fail closed before source read. Deterministic question parsing until generation consent. Disclose redacted payload, actual providers/tools if known, counts/cost uncertainty and destination before first LLM call; record GenerationConsent, then show exact outbound title/narration/URLs and approve board before media calls. State graph: NEW → AWAITING_INPUT (sessions,audience,duration,aspect,tone,voice in a compact prompt) → AWAITING_GENERATION_CONSENT → STORYBOARD_REVIEW → APPROVED → RENDERING → READY → DELIVERED. Edit from REVIEW/APPROVED returns REVIEW with approval cleared. Cancel→CANCELLED. Missing/expired input→WAITING_INPUT_EXPIRED; denied host permission→BLOCKED_APPROVAL; capability unavailable→BLOCKED_CAPABILITY; render exception→FAILED. Resume expired job re-prompts and requires fresh approval. Gateway messages are request/response: return prompt immediately, no sleeping. Cron/noninteractive invocation missing complete options and a matching user approval returns `NEEDS_INPUT`/exit 2 before ctx.llm or tools; supplied options are not consent. Ordinary prose supported through demo_reply with model extraction as data only; `/demo <prose>` always works without hooks. Concurrent replies CAS; duplicate approve/render never spends twice. D owns trusted platform identity mapping. No global default session.

### B: deterministic generative-art renderer
```python
# art.py
plan_art(snapshot: SessionSnapshot, board: Storyboard, *, width: int, height: int, fps: int = 24) -> ArtPlan  # seed from source fingerprint; ordered events drive motion
render_art_frame(plan: ArtPlan, events: tuple[Event,...], frame_index: int) -> Image.Image  # pure Pillow RGBA frame
render_art_sequence(plan: ArtPlan, events: tuple[Event,...], output_dir: Path) -> tuple[Path,...]  # bounded PNG sequence, no tools/network
```
Tool category→palette; normalized event time→particle birth; invocation counts→density; explicit error→pulse; known duration→trail length, unknown→neutral fixed length. Use a documented nonlinear compression of gaps and frame mapping so real-session timeline is visibly sped up. No invented metrics. Frame pixels must change when event order/status changes with seed fixed. Return art PNGs; C assembles video through host-dispatched terminal. 720x1280/1280x720 at 24fps v0; short sequence smoke probe; production 60s ≤90s. Art includes no raw transcript text. Optional p5/shaders deferred.

### C: host tools, compositor, delivery
```python
# media_tools.py
resolve_media_tools(ctx: Any) -> dict[str,str]  # actual available tool names/schema bindings; absent required tool raises CapabilityUnavailable
call_tool(ctx: Any, name: str, arguments: dict, *, policy: MediaPolicy) -> ToolResult  # ctx.dispatch_tool ONLY, bounded count/deadline, denial propagated
make_title(ctx: Any, board: Storyboard, job_dir: Path, *, policy: MediaPolicy) -> Asset  # image_generate, sanitized title, local validated raster
make_speech(ctx: Any, board: Storyboard, job_dir: Path, *, policy: MediaPolicy) -> tuple[Asset,...]  # one text_to_speech per scene, <=6; empty tuple only explicit voice-off
capture_screenshots(ctx: Any, board: Storyboard, job_dir: Path, *, policy: MediaPolicy) -> tuple[Asset,...]  # v1 browser navigate/screenshot through host registry, approved session URLs only
make_music(ctx: Any, board: Storyboard, job_dir: Path, *, policy: MediaPolicy) -> Asset | None  # optional registered provider or approved local licensed asset; no private credentials
# compositor.py
preflight_terminal(ctx: Any, job_dir: Path, *, policy: MediaPolicy) -> None  # random sentinel shared-filesystem check plus confirmed local backend and ffmpeg/ffprobe
freeze_render(snapshot: SessionSnapshot, board: Storyboard, approval: Approval, assets: tuple[Asset,...], art_plan: ArtPlan) -> RenderSnapshot  # validate/freeze hash-bound composition inputs
build_ffmpeg_argv(board: Storyboard, assets: tuple[Asset,...], art_frames: tuple[Path,...], output: Path) -> list[str]  # static argv/filter files, no transcript shell interpolation
compose(ctx: Any, snapshot: RenderSnapshot, job_dir: Path, *, policy: MediaPolicy) -> RenderBundle  # terminal dispatch runs argv safely, captions + timeline + visible art, TTS mix, duration cap
probe_video(ctx: Any, bundle: RenderBundle, *, policy: MediaPolicy) -> dict  # ffprobe and full ffmpeg decode via terminal; reject invalid/silent voice-on output
# delivery.py
format_delivery(bundle: RenderBundle) -> str  # caption + newline MEDIA:/absolute/job/output.mp4, no raw manifest or upload-to-X
validate_delivery(bundle: RenderBundle, job_root: Path) -> None  # existent confined file, hash, size and media type
```
image_generate/text_to_speech names verified against runtime catalog in D; adapter argument builders must use the installed schemas, not guessed model-facing arguments. Screenshot bindings likewise discovered; no direct vendor requests as fallback. Host permission failure is terminal for that action; never retry via raw HTTP/subprocess/alternate credentials. If CLI context cannot dispatch with approval, fail and offer `/demo render <job>` from interactive chat. Screenshot private/loopback/metadata addresses, userinfo and unsafe redirects blocked; require explicit URL whitelist. Caption/title/narration shown before network generation. Each scene TTS decoded duration must fit its scene interval; TTS > timeline duration must trigger paced script revision and reapproval, not truncate evidence/narration silently. Voice-off intentional video may omit audio; release probe must use voice-on. Music ducked under speech and optional. All ffmpeg/ffprobe execution is `ctx.dispatch_tool("terminal", {"command": shlex.join(argv)})`; no direct subprocess fallback. Write captions/filter contents to confined files. Require known local backend plus nonce shared-filesystem proof; unknown/remote fails closed. `freeze_render` includes art frame Assets in assets; composition consumes their hashes/paths, does not regenerate them. A shared job-wide max_calls=24 includes title, <=6 TTS, terminal preflight/composition/probing and optional v1 calls; reject excess before dispatch. Budget policy enforced centrally, not reset per adapter.

### D: integration / host adapters / release
```python
# __init__.py
register(ctx: Any) -> None  # command/tool/CLI only; no provider/network/render at import or register
# pipeline.py
render_job(ctx: Any, owner: Owner, job_id: str, *, policy: MediaPolicy) -> RenderBundle  # revalidate approval/CAS, orchestrate A→B→C, cached assets by board+input digest
# cli.py
setup_cli(parser: argparse.ArgumentParser) -> None  # register interview/status/render/probe subcommands
run_cli(args: argparse.Namespace) -> int  # JSON status, no unattended interaction, exit 0/2/3/4
# scripts/probe_outcome.py (implemented by D, not this design card)
main(argv: list[str] | None = None) -> int  # real-data acceptance, no synthetic substitute; JSON evidence + MP4/3 PNG
```
Exceptions: `ContractError`, `Conflict`, `NeedsInput`, `ApprovalRequired`, `CapabilityUnavailable`, `RenderFailed`, all declared in supplied schemas.py, maintained by D. Core methods raise; slash converts to safe text, tool to JSON, CLI to 2 input/approval, 3 capability, 4 render failure. No exception text containing secrets. Store render idempotency key `(job_id, board.digest)` with short lease; stale RENDERING becomes FAILED and requires explicit retry, never automatic double-spend. `timeout_s` caps host operations; if cancellation uncertain mark UNKNOWN, don't blindly retry. Asset paths have checksums, format checks, max size 24 MiB gateway target; if too large reencode boundedly or fail, never replace with a text-only success. Keep artifacts 7 days by default with explicit cleanup; never delete source DB. Owner scope guards status/resume/render/delivery.

## Parallel lane ownership and tiny-task sequence
D maintains the already-published schemas.py skeleton, __init__.py, pipeline.py, cli.py, plugin.yaml, README.md, scripts/, integration tests and shared fixtures. A owns session_reader.py, store.py, interview.py, storyboard.py and tests/test_interview.py, tests/test_sessions.py. B owns art.py and tests/test_art.py. C owns media_tools.py, compositor.py, delivery.py and tests/test_media.py, tests/test_compositor.py, tests/test_delivery.py. No edits to siblings' owned files. Freeze contracts here; All lanes import the supplied skeleton immediately; no bootstrap dependency on D. Do not commit duplicate schemas.py. Each lane starts by writing failing contract tests, runs them, implements smallest unit, reruns and commits; then exercises its real boundary and records output.

A builder ~6h: reader/privacy+evidence (2h), store/draft/consent (2h), board/tests (2h). B kilo ~3h: art mapping (1h), sequence/performance (1h), sensitivity/PNG (1h). C kilo ~5h: adapters+preflight (2h), composition/audio/delivery (2h), failure tests (1h). D builder ~6h after A–C. These are estimates, not measured capacity; Kilo finishing receipts is a schedule risk. If C is not started by Nov 5 noon PT, coordinator must reassess/reassign via Kanban; no automatic dropped scope or silent date promise. Separate scratch clones/branches (`lane/a-interview`, `lane/b-art`, `lane/c-media`) from the pinned public commit; integration cherry-picks reviewed commits. No force-push or concurrent shared-workspace checkout. No live core/config writes.

D t_dfb8f448 integrates after all lane parents complete: validate supplied schema, import modules, register host boundaries, validate+doctor, pinned clean install, real probe and README. Integration must independently rerun tests, not trust lane claims. Any web-facing UI requires actual browser console/network/layout verification; no UI in v0 so explicitly mark gate N/A. Screenshot capture itself requires real browser evidence in v1.

## Outcome verification probe — future acceptance, NOT EXECUTED here
D implements the following exact interface. Run in repo root, with host permission available; failing prerequisites exit nonzero and no synthetic fallback. `--interactive` prompts the human to review redactions, options, storyboard and provider disclosures; cannot be used to auto-approve cron. An existing approval can instead be supplied only if owner and digest match. DB source is explicit and read-only. No copied credentials in disposable home. Host-provided provider credentials may be explicitly passed by the operator; absent capability blocks.
```bash
python3 scripts/probe_outcome.py \
  --source-db "$HOME/.hermes/profiles/hive/state.db" \
  --source-profile hive --session-id "${HIVE_SESSION_ID:?Set explicit verified Hive session ID}" \
  --isolated-home .probe/home --output-dir .probe/output \
  --duration 60 --aspect 9:16 --voice on --interactive \
  --require title,tts,art,timeline,captions,delivery --max-seconds 90
# CANONICAL all-non-negotiables gate (v1): explicitly select a real touched-public-URL Hive session first.
: "${HIVE_PUBLIC_URL_SESSION_ID:?Set an explicit verified Hive session ID with a public URL}"
python3 scripts/probe_outcome.py \
  --source-db "$HOME/.hermes/profiles/hive/state.db" \
  --source-profile hive --session-id "$HIVE_PUBLIC_URL_SESSION_ID" \
  --isolated-home .probe/full-home --output-dir .probe/full-output \
  --duration 60 --aspect 16:9 --voice on --interactive \
  --require title,tts,screenshot,art,timeline,captions,delivery --max-seconds 90
```
The v0 command pins an actually observed Hive profile DB/session: read-only metadata check found 11 tool calls and profile_name=hive on Nov4. Full-capability command requires a separately explicit URL-bearing session; no URL-bearing session is claimed verified here. Reject profile/source mismatch; never scan profiles or substitute another session. Use one read-only SQLite snapshot, record selected ID/fingerprint privately. Disposable home must not yet exist; cleanup only its owned data. Run disposable `hermes plugins validate <repo> --install-deps`, `hermes plugins doctor <repo> --ci`, tests and exact public-SHA install before plugin CLI/command invocation. Output gate checks: positive duration≤90, H264 video/AAC audio, full ffmpeg decode exit 0, audible decoded voice-on signal mean>-50dBFS, captions visible at 3 sample frames, actual art frames composited (ablation comparison), accelerated timeline event count/source references, successful title+TTS dispatch trace with file hashes and actual speech, optional screenshot provenance. No fake TTS/silent tone can satisfy narration. Check board approval digest/owner, image title/title-card presence, selected real DB events, caption length/tag, adapter `extract_media` returns exact existing MP4 and strips marker; test Discord/Telegram/Desktop actual supported media surfaces in isolated harness. Local extractor proof alone is not actual external delivery: release requires an actual playable video delivered to the originating authorized chat and DeliveryReceipt. Without an authorized connected chat, block release rather than passing transport unexercised. Other platform mocks are coverage only; report each actually exercised platform. Include denied/unattended/expired/edit/duplicate/concurrent/cross-owner tests proving zero paid dispatch before authorization. Attach real MP4 + three PNG grabs to D completion; never attach MD/ZIP. Private evidence JSON/selected snapshots stay untracked and never public fixtures.

## Cost Summary
v0 uses at most one storyboard LLM draft, one generated title and at most six per-scene TTS calls per approved digest, plus bounded edit drafts; music off. Exact provider prices unknown until host routing is known: do not invent dollar estimates or claim free. Show tool names, provider estimates if supplied and call counts before approval; reject policy limits, do not silently expand them. Human-equivalent planning estimate 20 engineering hours across lanes, not measured cost. Act 1 builds boundaries (~14h), Act 2 verifies integration/privacy/media (~6h); no defensible dollar ratio without rates.

## Autoreason record — one round, time-box complete
Read local `autoreason-moa` framework (named `autoreason` unavailable); explicit user A/B/AB procedure governs. Critic, author B, synthesizer and final judges used fresh isolated contexts. A is incumbent/do-nothing (not duplicated). B adds a prepared-action framework; AB retains correctness fixes without that framework. Synthesizer was explicitly constrained by orchestrator to favor simple consent and early schemas; not an independently invented option. Initial summary-only ballots were biased by differing detail and superseded, not counted. Final judges read all full artifacts (A plus normative B/AB overrides), not critique files. Candidate provenance filenames remained visible; label/order blinding reduces order bias but is not perfect identity blinding. Single model route, not independent model-family validation.

| Final fresh judge | Label mapping/order | Ranking | A | B | AB |
|---|---|---|---:|---:|---:|
| Correctness/complexity | X=A,Y=AB,Z=B | Y>Z>X | 0 | 1 | 2 |
| Nov6 feasibility | X=B,Y=A,Z=AB | Z>X>Y | 0 | 1 | 2 |
| Safety/integration | X=AB,Y=B,Z=A | X>Y>Z | 0 | 1 | 2 |
| **Borda total** | 2/1/0 | **AB>B>A** | **0** | **3** | **6** |

Tie rule: retain incumbent A, otherwise smaller scope. No tie. No second revision round; work completed within 90 minutes. Final adoption corrects partial persistence, immutable evidence/render snapshots, CAS vs board revision, video asset typing, generation consent, scene TTS, terminal locality and real delivery gate. Small orchestrator post-ballot clarifications: shared budget 24; accidental six-second ceiling removed (six CALLS, each fitting its scene, remains); explicit actual Hive DB/id probe; exact consent prototype and immutable evidence load/store prototypes. Judges warned estimates do not establish deadline capacity; integration must surface slip rather than assert feasibility as fact. No plugin code beyond schema declarations, no rendered MP4 claimed on this architecture card.
