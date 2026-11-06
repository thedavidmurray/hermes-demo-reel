# Hermes Demo Reel

**v0 release candidate — real CLI media verified, independent review pending.** Turns explicitly selected,
read-only Hermes session evidence into an approved storyboard and a ≤90-second
demo reel. No X posting. Receipts remains the main hackathon entry.

## Install / prerequisites

The public repository is `thedavidmurray/hermes-demo-reel`. Do not treat `main`
as a release until the integration branch has passed the gates below. For an
integration preview, install an explicitly reviewed commit using the installed
Hermes CLI's pinned GitHub install syntax (`hermes plugins install --help`).
Test in a fresh disposable `HERMES_HOME`; do not reuse a live profile.

Requires Python 3.10+, Pillow, local ffmpeg/ffprobe, and a Hermes version with
`ctx.llm`, `ctx.dispatch_tool`, slash-command registration, CLI registration and
plugin storage. The host must enable plugin LLM access and approve its normal
tool calls. This plugin does not load credentials from other profiles or modify
Hermes core/configuration. Remote/unknown terminal backends are rejected.

## Chat workflow

1. `/demo` starts/resumes a persisted short interview. Select the explicit source
   DB and session IDs, audience, 15–90-second duration, portrait/landscape, tone,
   and voice. Ambiguous source selection is rejected, never guessed.
2. Review the source/redaction disclosure and use the exact generation-consent
   command displayed by the interview. New captures use v2: bounded, best-effort
   redacted excerpts of the user ask and recorded tool inputs/outputs are sent
   to the storyboard LLM. Inspect the **exact excerpt catalog** before consent;
   this is more source content than legacy v1 metadata, not a privacy guarantee.
   Historical assistant reasoning is not captured, and historical file paths
   are never followed to fetch more data.
3. Review the storyboard, captions, narration, and tool disclosure. Use the
   displayed approval hashes, or edit before approving.
4. `/demo render JOB` renders the approved board. It creates session-driven art,
   a generated title, scene-aligned speech when enabled, captions, and timeline
   overlays, then composes/probes the result with host-dispatched ffmpeg.
5. `/demo deliver JOB` requests transport to that same originating chat. It
   requires an actual media acknowledgement; returning a local file path alone
   is not delivery. The caption contains `@NousResearch`; you post it yourself.

Job identity is captured from the trusted command invocation, not model tool
arguments. The model-facing `demo_reply` tool only explains how to continue;
it cannot approve sources, spend, or sends. Slash answers are required in this
preview. Cron/noninteractive actions fail immediately instead of asking for input.

## Rule-13 disclosure: data, tools, network and cost

- **Reads:** only explicitly selected session rows from the provided `state.db`,
  through read-only SQLite, including user requests, tool-event metadata and
  bounded tool inputs/outputs (`write_file`, `patch`, `skill_manage` patch text,
  terminal commands and recorded tool results). Does not fetch historical paths,
  scan other profiles or write the source database. Redaction is conservative,
  not a guarantee against all private information: inspect the full excerpt
  catalog before LLM consent and the exact board before media approval.
- **Stores:** owner-scoped interviews, immutable redacted snapshots, consents,
  storyboard approvals and durable render/send claims in Hermes plugin storage.
  Generated assets/manifests remain in owner/job-scoped plugin data directories.
  **Automatic seven-day cleanup is not yet implemented**; remove only the
  plugin's own job data manually when no longer needed. Never delete source DBs.
- **LLM:** `ctx.llm.complete` for storyboard planning, after explicit consent.
  This may contact the host-configured provider and incur its normal charges.
- **Host tools:** `image_generate` for the title; `text_to_speech` for at most
  six scene-aligned narrations; `terminal` for local ffmpeg/ffprobe, shared-FS
  preflight, and guarded title-image download; `send_message` for same-chat
  Discord/Telegram media transport. Calls use `ctx.dispatch_tool`, preserving
  host approvals and credentials. No raw provider SDK or alternate keys.
- **Additional network:** a generated HTTPS title URL can be downloaded through
  host terminal dispatch with guarded DNS/IP validation, pinned resolution and
  no redirects. No upload to X, analytics endpoint, or plugin-owned provider.
- **Generative art:** local deterministic Pillow frames driven by frozen session
  events/order/status; no network. Their hashes are frozen before composition.
- **Budget:** one storyboard draft, one title, up to six speech calls; one shared
  maximum 24 media/terminal dispatches. Provider pricing depends on your host
  routing and is not represented as free. Failed/uncertain paid calls are not
  automatically retried. Cancel/start a new approved job after investigation.

## Current limitations / release gates

- Browser screenshots, music and templates remain the Nov 9 v1 phase; requests
  for screenshots/music fail closed in v0. No browser capture is claimed yet.
- **Live Discord/Telegram transport not yet verified end-to-end; CLI path verified with real providers.**
  Desktop native media acknowledgement is not implemented. There was no live-chat
  release test; that requires an explicitly authorized destination. The ordinary
  Kanban artifact notifier is not a plugin transport test.
- Locality currently uses the installed terminal's private backend resolver;
  source identity uses installed gateway `SessionSource`. Validation warns about
  these internal API imports: compatibility debt, not a warning-free catalog claim.
- The original Nov 5 CLI reel failed output-quality review despite technical
  validation. Its replacement uses five LLM-scripted scenes (ask → three work
  moments → result), frozen source panels, 3-second caption beats and an outcome
  end card, with abstract art only behind the content. It is a real 30.0s
  1280×720 H.264/AAC render using host title generation and **five TTS calls**;
  the full-decode probe measures -23.2 dB mean audio level. Three PNG grabs were
  visually inspected. **Independent narrative/privacy/media approval is still
  required; passing an encode probe is not a taste or showcase endorsement.**
  Citations establish provenance, not semantic truth; approval must check that
  the visible excerpt actually supports each claim. Panels show excerpts, not
  live recordings. Legacy schema-v1 jobs remain readable.
- FAL returned HTTP401 on a separate title attempt; that job remains durably
  UNKNOWN and was not retried. A new approved job used the operator-authorized
  `image_gen.provider: openai-codex`, `use_gateway: true` configuration.
- Independent Critic approval and the `v0.1.0` tag remain pending. The showcase
  separately requires thesis → beat-map → animatic → council → final, with no
  single-render quality claim. No catalog PR has been authorized or opened.
- Validation status must include its actual output: an exit zero alone is not
  proof that `--install-deps` succeeded. The installed Hermes launcher can also
  auto-complete an interrupted source update before executing a command; a
  timed-out launch is not a doctor result. Do not repair live core to pass gates.

## Authorized local operator workflow

The local CLI uses the installed host context, not a direct provider client.
Never run paid stages as standalone `python cli.py`. Read each generated
redaction/storyboard file before supplying its exact consent hash:

```sh
hermes demo-reel interview --operator YOUR_NAME --answers examples/v0-answers.json \
  --db /absolute/path/to/state.db --session-id YOUR_SESSION --consent-source
hermes demo-reel draft --operator YOUR_NAME --job JOB --consent-generation HASH
hermes demo-reel render --operator YOUR_NAME --job JOB \
  --approve-board BOARD_HASH --approve-disclosure DISCLOSURE_HASH
python scripts/probe_outcome.py --mp4 /path/returned/by/render.mp4
```

Use a disposable `HERMES_HOME` for verification. The answers file deliberately
contains no source database, session identifiers or consent defaults. Missing
inputs exit 2 with `NEEDS_INPUT`, including in unattended runs. A CLI operator
assertion is local authority, not evidence of a human chat approval. `render`
returns the MP4, PNG and caption paths; it does not send a live-chat message.

## Development / unattended checks

```sh
python3 -m unittest discover -s tests -v
python3 cli.py check
python3 cli.py interview  # immediate NEEDS_INPUT JSON, exit 2
```

`check` only verifies ffmpeg/ffprobe availability; it explicitly does **not**
attest real providers, chat transport, or release readiness. There is no UI/browser
layout surface in v0. Tests use synthetic sources and fake provider boundaries
except where they explicitly run local ffmpeg fixtures.
