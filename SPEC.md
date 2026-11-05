# Demo Reel implementation contract — candidate A

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

Manifest target: `name: demo-reel`, `version: 0.1.0`, `requires_hermes: '>=0.21.7'`, `python_dependencies: ['Pillow>=10,<13']`; register command `demo`, CLI `demo`, tool `demo_reply`. Manifest capabilities must use parser-supported names for LLM access; D confirms exact supported spelling before release. No guessed capability strings, no required provider env vars, no secret reading and no hook that intercepts unrelated chats. Desktop uses command/media output; optional pane is SDK-only and deferred.

## Frozen wire contracts (schema_version = 1)
All below are Python dataclasses serialized as UTF-8 canonical JSON (sorted keys, compact separators, no NaN). Fields listed are required unless `?`. `schemas.py` belongs to integration D; lanes may consume these exact shapes with local test fixtures but may not independently redesign them. Tuple fields serialize to arrays; Path fields to resolved strings. Artifact JSON stays in plugin data, never chat attachments.

- `Owner(profile: str, session_id: str, platform: str, conversation_id: str, user_id: str)` — opaque scope, never inferred from transcript text.
- `DemoOptions(session_ids: tuple[str,...], audience: str, duration_s: int, aspect: Literal['9:16','16:9'], tone: str, voice: bool, screenshots: bool=False, music: bool=False)` — duration 15..90, at most 3 sessions; default suggested 60s but never approved implicitly.
- `Event(event_id: str, session_id: str, row_id: int, t_ms: int, kind: str, tool: str, status: Literal['ok','error','unknown'], label: str, duration_ms: int|None)` — only redacted allowlisted labels; no raw arguments, credentials or tool results.
- `SessionSnapshot(schema_version: int, source_sha256: str, selected_session_ids: tuple[str,...], events: tuple[Event,...], safe_summary: str, public_urls: tuple[str,...])` — fingerprint of canonical selected rows in one read transaction, not changing DB bytes. URLs are candidates, not approved destinations.
- `Scene(scene_id: str, start_ms: int, end_ms: int, title: str, caption: str, narration: str, event_ids: tuple[str,...], visual: Literal['title','timeline','art','screenshot'])`.
- `Storyboard(schema_version: int, job_id: str, revision: int, source_sha256: str, options: DemoOptions, scenes: tuple[Scene,...], x_caption: str, digest: str)` — contiguous intervals from zero; total ≤ requested duration; X caption ≤280 Unicode codepoints with tag. Digest SHA256 of all fields except digest.
- `Approval(owner: Owner, storyboard_digest: str, revision: int, approved_at: str, allowed_external_tools: tuple[str,...], approved_urls: tuple[str,...])` — explicitly recorded from trusted current user action, never LLM/session content.
- `Job(schema_version: int, job_id: str, owner: Owner, state: str, revision: int, options: DemoOptions|None, board: Storyboard|None, approval: Approval|None, expires_at: str, error_code: str|None)`.
- `Asset(asset_id: str, kind: Literal['title','speech','screenshot','music','art'], path: Path, sha256: str, duration_ms: int|None, provenance_event_ids: tuple[str,...], dispatch_id: str|None)`.
- `ArtPlan(seed: int, source_sha256: str, event_ids: tuple[str,...], width: int, height: int, fps: int, duration_ms: int)`.
- `DispatchRecord(dispatch_id: str, tool: str, args_sha256: str, status: str, asset_sha256: str|None, elapsed_ms: int)` — never raw prompts/results in logs.
- `RenderBundle(job_id: str, board_digest: str, mp4: Asset, frames: tuple[Asset,...], x_caption: str, manifest_path: Path)` — frames exactly 3 PNGs, video H264 yuv420p/AAC, faststart.
- `CommandReply(text: str, job_id: str|None, state: str, code: str)`; `Edit(scene_id: str, field: Literal['title','caption','narration'], value: str)`.
- `MediaPolicy(timeout_s: int=120, max_calls: int=12, network_allowed: bool=False, approved_urls: tuple[str,...]=())`; `ToolResult(ok: bool, dispatch_id: str, data: dict, error_code: str|None)`.

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
save_job(job: Job, *, expected_revision: int) -> Job  # atomic compare-and-swap, increment revision or Conflict
# interview.py
start_interview(owner: Owner, *, now: datetime) -> Job  # persist draft, 30-minute TTL
advance_interview(job: Job, answer: str, *, now: datetime) -> CommandReply  # bounded single-step NL input, no waiting loop
handle_demo(ctx: Any, raw_args: str, *, session_id: str = '', **kwargs) -> str  # slash callback; /demo <answer>, approve <digest>, edit..., resume <id>, cancel
handle_reply(ctx: Any, args: dict, **kwargs) -> str  # demo_reply tool JSON wrapper for natural follow-up, trusted owner from host only
expire_job(job: Job, *, now: datetime) -> Job  # WAITING_INPUT_EXPIRED; never synthesize approval
# storyboard.py
build_storyboard(ctx: Any, snapshot: SessionSnapshot, options: DemoOptions, *, job_id: str) -> Storyboard  # ctx.llm draft from redacted evidence, validate structured output
validate_storyboard(board: Storyboard, snapshot: SessionSnapshot) -> None  # reject unsupported claims, unknown event refs, interval/duration violations
edit_storyboard(board: Storyboard, edits: tuple[Edit,...]) -> Storyboard  # bump revision/digest; invalidate approval at job boundary
approve_storyboard(job: Job, *, owner: Owner, digest: str, now: datetime) -> Job  # exact revision + owner, required disclosure consent
```
State graph: NEW → AWAITING_INPUT (sessions,audience,duration,aspect,tone,voice in a compact prompt) → STORYBOARD_REVIEW → APPROVED → RENDERING → READY → DELIVERED. Edit from REVIEW/APPROVED returns REVIEW with approval cleared. Cancel→CANCELLED. Missing/expired input→WAITING_INPUT_EXPIRED; denied host permission→BLOCKED_APPROVAL; capability unavailable→BLOCKED_CAPABILITY; render exception→FAILED. Resume expired job re-prompts and requires fresh approval. Gateway messages are request/response: return prompt immediately, no sleeping. Cron/noninteractive invocation missing complete options and a matching user approval returns `NEEDS_INPUT`/exit 2 before ctx.llm or tools; supplied options are not consent. Ordinary prose supported through demo_reply with model extraction as data only; `/demo <prose>` always works without hooks. Concurrent replies CAS; duplicate approve/render never spends twice. D owns trusted platform identity mapping. No global default session.

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
make_speech(ctx: Any, board: Storyboard, job_dir: Path, *, policy: MediaPolicy) -> Asset | None  # text_to_speech; None only explicit voice-off
capture_screenshots(ctx: Any, board: Storyboard, job_dir: Path, *, policy: MediaPolicy) -> tuple[Asset,...]  # v1 browser navigate/screenshot through host registry, approved session URLs only
make_music(ctx: Any, board: Storyboard, job_dir: Path, *, policy: MediaPolicy) -> Asset | None  # optional registered provider or approved local licensed asset; no private credentials
# compositor.py
build_ffmpeg_argv(board: Storyboard, assets: tuple[Asset,...], art_frames: tuple[Path,...], output: Path) -> list[str]  # static argv/filter files, no transcript shell interpolation
compose(ctx: Any, board: Storyboard, assets: tuple[Asset,...], art_frames: tuple[Path,...], job_dir: Path, *, policy: MediaPolicy) -> RenderBundle  # terminal dispatch runs argv safely, captions + timeline + visible art, TTS mix, duration cap
probe_video(ctx: Any, bundle: RenderBundle, *, policy: MediaPolicy) -> dict  # ffprobe and full ffmpeg decode via terminal; reject invalid/silent voice-on output
# delivery.py
format_delivery(bundle: RenderBundle) -> str  # caption + newline MEDIA:/absolute/job/output.mp4, no raw manifest or upload-to-X
validate_delivery(bundle: RenderBundle, job_root: Path) -> None  # existent confined file, hash, size and media type
```
image_generate/text_to_speech names verified against runtime catalog in D; adapter argument builders must use the installed schemas, not guessed model-facing arguments. Screenshot bindings likewise discovered; no direct vendor requests as fallback. Host permission failure is terminal for that action; never retry via raw HTTP/subprocess/alternate credentials. If CLI context cannot dispatch with approval, fail and offer `/demo render <job>` from interactive chat. Screenshot private/loopback/metadata addresses, userinfo and unsafe redirects blocked; require explicit URL whitelist. Caption/title/narration shown before network generation. TTS > timeline duration must trigger paced script revision and reapproval, not truncate evidence/narration silently. Voice-off intentional video may omit audio; release probe must use voice-on. Music ducked under speech and optional.

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
Exceptions: `ContractError`, `Conflict`, `NeedsInput`, `ApprovalRequired`, `CapabilityUnavailable`, `RenderFailed`, all defined in schemas.py by D. Core methods raise; slash converts to safe text, tool to JSON, CLI to 2 input/approval, 3 capability, 4 render failure. No exception text containing secrets. Store render idempotency key `(job_id, board.digest)` with short lease; stale RENDERING becomes FAILED and requires explicit retry, never automatic double-spend. `timeout_s` caps host operations; if cancellation uncertain mark UNKNOWN, don't blindly retry. Asset paths have checksums, format checks, max size 24 MiB gateway target; if too large reencode boundedly or fail, never replace with a text-only success. Keep artifacts 7 days by default with explicit cleanup; never delete source DB. Owner scope guards status/resume/render/delivery.

## Parallel lane ownership and tiny-task sequence
D owns schemas.py, __init__.py, pipeline.py, cli.py, plugin.yaml, README.md, scripts/, integration tests and shared fixtures. A owns session_reader.py, store.py, interview.py, storyboard.py and tests/test_interview.py, tests/test_sessions.py. B owns art.py and tests/test_art.py. C owns media_tools.py, compositor.py, delivery.py and tests/test_media.py, tests/test_compositor.py, tests/test_delivery.py. No edits to siblings' owned files. Freeze contracts here; D creates dataclasses from this contract on integration. Independent lanes can use local fixture builders matching it; do not commit duplicate schemas.py. Each lane starts by writing failing contract tests, runs them, implements smallest unit, reruns and commits; then exercises its real boundary and records output.

A builder ≤8h: reader+privacy (2h), store/state/reply (2h), board/edit/approval (2h), tests+handoff (2h). B kilo ≤6h: pure frame mapping (2h), sequence/performance (2h), sensitivity tests+PNG (2h). C builder ≤8h: tool adapters and privacy (2h), ffmpeg audio/captions/art (3h), delivery+failure tests (3h). Builder lanes may queue; integration owner must flag if this threatens Nov 6, rather than drop requirements. Work in separate clones/branches (`lane/a-interview`, `lane/b-art`, `lane/c-media`) from public main, not concurrent checkouts in shared workspace. Push branches only, integration cherry-picks reviewed commits; no force pushes. No lane modifies shared/live core or configs.

D t_dfb8f448 integrates after all lane parents complete: define schema, import modules, register host boundaries, validate+doctor, pinned clean install, real probe and README. Integration must independently rerun tests, not trust lane claims. Any web-facing UI requires actual browser console/network/layout verification; no UI in v0 so explicitly mark gate N/A. Screenshot capture itself requires real browser evidence in v1.

## Outcome verification probe — future acceptance, NOT EXECUTED here
D implements the following exact interface. Run in repo root, with host permission available; failing prerequisites exit nonzero and no synthetic fallback. `--interactive` prompts the human to review redactions, options, storyboard and provider disclosures; cannot be used to auto-approve cron. An existing approval can instead be supplied only if owner and digest match. DB source is explicit and read-only. No copied credentials in disposable home. Host-provided provider credentials may be explicitly passed by the operator; absent capability blocks.
```bash
python3 scripts/probe_outcome.py \
  --source-db /Users/djm/.hermes/state.db \
  --source-profile hive --select-real-session latest-with-tools \
  --isolated-home .probe/home --output-dir .probe/output \
  --duration 60 --aspect 9:16 --voice on --interactive \
  --require title,tts,art,timeline,captions,delivery --max-seconds 90
# full-capabilities v1 gate, requires a real touched PUBLIC URL and browser approval:
python3 scripts/probe_outcome.py \
  --source-db /Users/djm/.hermes/state.db \
  --source-profile hive --select-real-session latest-with-public-url \
  --isolated-home .probe/full-home --output-dir .probe/full-output \
  --duration 60 --aspect 16:9 --voice on --interactive \
  --require title,tts,screenshot,art,timeline,captions,delivery --max-seconds 90
```
Probe must verify actual profile/source mapping before reading: Hive is default-profile coordinator in existing topology; if metadata contradicts, require explicit corrected source, never scan all profiles. Use one read-only SQLite snapshot, record selected ID/fingerprint privately. Disposable home must not yet exist; cleanup only its owned data. Run disposable `hermes plugins validate <repo> --install-deps`, `hermes plugins doctor <repo> --ci`, tests and exact public-SHA install before plugin CLI/command invocation. Output gate checks: positive duration≤90, H264 video/AAC audio, full ffmpeg decode exit 0, audible decoded voice-on signal mean>-50dBFS, captions visible at 3 sample frames, actual art frames composited (ablation comparison), accelerated timeline event count/source references, successful title+TTS dispatch trace with file hashes and actual speech, optional screenshot provenance. No fake TTS/silent tone can satisfy narration. Check board approval digest/owner, image title/title-card presence, selected real DB events, caption length/tag, adapter `extract_media` returns exact existing MP4 and strips marker; test Discord/Telegram/Desktop actual supported media surfaces in isolated harness. Local extractor proof alone is not actual external delivery; label transport not exercised if no authorized connected chat. Include denied/unattended/expired/edit/duplicate/concurrent/cross-owner tests proving zero paid dispatch before authorization. Attach real MP4 + three PNG grabs to D completion; never attach MD/ZIP. Private evidence JSON/selected snapshots stay untracked and never public fixtures.

## Cost Summary
v0 uses at most one storyboard LLM draft, one generated title and one TTS call per approved digest, plus bounded edit drafts; music off. Exact provider prices unknown until host routing is known: do not invent dollar estimates or claim free. Show tool names, provider estimates if supplied and call counts before approval; reject policy limits, do not silently expand them. Human-equivalent planning estimate 22 engineering hours across lanes, not measured cost. Act 1 builds boundaries (~14h), Act 2 verifies integration/privacy/media (~8h); no defensible dollar ratio without rates.

## Autoreason record
Pending one time-boxed A/B/AB round, at most 90 minutes. A itself is the do-nothing option, not a duplicated fourth ballot. Three fresh-context judges, cyclic label permutations, Borda 2/1/0, ties retain A then prefer smallest scope. Hard constraints dominate preferences; inadmissible candidate excluded. Critic, author B and synthesizer isolated; judges see only anonymized candidate artifacts and requirements, never critiques.
