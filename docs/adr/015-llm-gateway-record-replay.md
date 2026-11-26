# ADR 015: One LLM gateway with record/replay, a token budget and no sampling parameters

- **Status:** accepted (M4, 2026-11-26)
- **Context milestone:** M4 (agents and workflow)

## Context

M4 adds the first LLM calls: the Intake agent, the Analyst agent and the failure explainer.
Four constraints shape how they are made:

1. **Cost and repeatability.** `make demo` must run on a clean machine without an API key, and
   tests and CI must never call the API.
2. **Determinism.** The architecture asked for "temperature 0" for extraction, but
   `temperature` is not available as a control:
   - **The docs.** [API versions](https://platform.claude.com/docs/en/api/versioning) says that
     in API version 2026-10-21 "the `temperature`, `top_p`, and `top_k` request parameters are
     removed. A request that sends any of them returns a 400 error on every model".
     [Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking) adds that on
     Sonnet 5.5 and Haiku 5.5, non-default values return a 400 on earlier API versions too.
   - **The SDK.** `anthropic` 2.14 (our lockfile) no longer has the keyword:
     `Messages.create() got an unexpected keyword argument 'temperature'`.
   - **The live API.** Sending it anyway through `extra_body` on 2026-11-26 returned the same
     response from both models:
     `400 invalid_request_error: temperature: Extra inputs are not permitted`.
3. **Structured output.** Forced `tool_choice` returns a 400 on Sonnet 5.5, so it cannot be
   used to force a schema either.
4. **Governance.** Model ids come only from configuration, and the audit trail must show which
   model answered.

## Decision

All calls go through `copilot.llm.gateway.Gateway`. Agents never import the SDK.

- **Model.** Each prompt (`src/copilot/prompts/*.md`) declares a tier (`main` or `fast`) in
  its front-matter. The gateway maps the tier to `COPILOT_MODEL_MAIN` or `COPILOT_MODEL_FAST`.
  The failure explainer (and later the narrative writer) uses the fast tier.
- **Output.** Every output is a Pydantic model sent as `output_config.format`, a JSON schema
  produced by the SDK's `transform_schema`. If validation fails, the model gets one retry
  with the error, then the call escalates (`LlmEscalation`) and the workflow routes it to the
  next human gate. Nothing is coerced silently. Every field of an output schema is required,
  so the model cannot leave fields out and pack everything into one.
- **Code checks on content.** `Gateway.call(..., validate=fn)` runs a code check on the parsed
  output. A failed check is handled like a schema error: one retry with the reason, then
  escalation. The failure explainer uses it for the number guard (`copilot.guards.numbers`:
  every number must come from the input) and for the cause wording. The M5 narrative writer
  reuses the same guard.
- **Depth.** Depth is set with `output_config.effort`, per prompt (`low` for plans and
  explanations, `medium` for intake), instead of temperature. No sampling parameters are
  sent.
- **Stop reasons.** `refusal` and `max_tokens` escalate. Server-side fallbacks to another
  model are **off**: a fallback would put a model that was never configured into the audit
  trail.
- **Budget.** Each run has a token budget (`COPILOT_RUN_TOKEN_BUDGET`, default 150,000). It
  is checked before every call (spent so far + the prompt's `max_tokens` must fit) and updated
  from the actual usage after it. An overrun is a hard stop and goes to a human gate with
  hold or reject (no retry). The budget is part of the run state, so it survives a resume.
- **Accounting.** Every call writes an `llm_call` audit event with:
  - prompt id and version
  - the model requested and the model that served
  - attempts, stop reason
  - input, output and cache tokens
  - cost and the pricing status

  Prices are in `config/llm_pricing.yaml`. They were verified against the
  [official pricing page](https://platform.claude.com/docs/en/about-claude/pricing) on
  2026-11-26, the date recorded there with `status: verified`. The same day,
  `copilot llm check-models` confirmed that both configured ids exist (checked again live, and
  the prices against the page again, when M4 closed). A price change means editing the file
  and the date.
- **Modes** (`COPILOT_LLM_MODE`, or `--live` / `--record` on the CLI):
  - `replay` (default) answers from `demo/recordings/<scenario>/<key>.json`. The key is the
    SHA-256 of the canonical request: model, prompt id@version, system prompt, messages, schema,
    effort and max_tokens. A missing recording is an error that names the prompt id@version and
    `--record`; it never falls back to a live call. The CLI prints the message and exits with
    code 2.
  - `record` calls the API once and stores the response text, stop reason, served model and
    usage. It stores no headers, keys or request ids.
  - `live` calls the API without storing anything.

  `live` and `record` are refused when `CI` is set. The test suite also replaces the SDK client
  with one that fails the test.
- **Stable inputs.** Prompts receive only stable inputs: the glossary, metadata about earlier
  submissions, the request text and the answers. Request ids, timestamps and machine-specific
  data (such as past runs on this machine) stay out of prompts, so replay keys are
  reproducible. Past runs are shown at G1 instead.

## Consequences

- `make demo` replays S1 and S2 with no API key and no cost. The recordings are part of the
  repository and change only through an explicit `--record` run.
- Changing a prompt, a model id or the glossary invalidates the affected recordings, and replay
  fails loudly until they are re-recorded. That is deliberate: a recording proves which prompt
  version produced which answer.
- **Prompt lock.** `tests/fixtures/prompt_lock.yaml` pins the SHA-256 of each prompt body per
  `id@version`. A body edited without a version bump fails the tests before replay does, and
  a test fails if any committed recording was made with an older prompt version. Re-recording
  one scenario (for example S2 for `failure_explainer@1.1.0`) leaves the others byte for byte
  unchanged.
- Recordings, the audit log, payloads, manifests and the delivery file are UTF-8 with the
  original characters and LF line endings. Only console output replaces characters that a
  legacy Windows console cannot print.
- Replay reproduces the decisions the models made, not fresh model behaviour. Behaviour
  against the live models is measured by `make eval` (M7). Reruns prove the numbers, not the
  prose (ADR 010).
- Explanations written by the LLM are stored outside the validation report. The report hash
  stays deterministic, and `copilot rerun` does not need the LLM.
