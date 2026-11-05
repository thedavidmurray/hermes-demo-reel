# Hermes Demo Reel

**Integration preview — not a verified release.** Turns explicitly selected,
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
   command displayed by the interview. Only redacted structured evidence goes
   to the storyboard LLM, not arbitrary historical transcript prose.
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
  through read-only SQLite, including structured tool-event metadata. Does not
  scan other profiles or write the source database. Redaction is conservative,
  not a guarantee against all private information: inspect the disclosed board.
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
- Native Desktop/CLI playable-media acknowledgement is not implemented. These
  surfaces cannot claim delivery; actual originating-chat transport remains a
  release gate. Discord/Telegram ACK checks exist but are not live-verified.
- Locality currently uses the installed terminal's private backend resolver;
  source identity uses installed gateway `SessionSource`. Doctor warns about
  these internal API imports. This compatibility debt is not a clean doctor claim.
- End-to-end outcome probe and pinned disposable GitHub install are not yet
  verified. Synthetic compositor fixtures are not real-session narration proof.
- Real approved Hive title/TTS, a ≤90-second audible MP4, three PNG frame grabs,
  actual same-chat delivery receipt and independent integration review are still
  required before release. No catalog PR has been authorized or opened.
- Dependency-install verification was blocked by host disk exhaustion. Validation
  status must include its actual output; an exit zero alone is not proof that
  `--install-deps` succeeded.

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
