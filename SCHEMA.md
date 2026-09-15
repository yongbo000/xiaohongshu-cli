# Structured Output Schema

`xiaohongshu-cli` uses a shared agent-friendly envelope for machine-readable output.

## Success

```yaml
ok: true
schema_version: "1"
data: ...
```

## Error

```yaml
ok: false
schema_version: "1"
error:
  code: not_authenticated
  message: need login
```

## Notes

- `--yaml` and `--json` both use this envelope
- non-TTY stdout defaults to YAML
- reading and search commands return their payload under `data`
- `status` returns `data.authenticated` plus `data.user`
- `whoami` returns `data.user`
- common `error.code` values include `not_authenticated`, `verification_required`, `ip_blocked`, `signature_error`, `unsupported_operation`, and `api_error`

## Compact projections

`--compact` (and `read --no-media` / `--fields`) only trims the structured payload inside `data`; the envelope is unchanged and the default (no switches) output is byte-identical to the raw API payload.

`comments` / `sub-comments --compact` shape:

```yaml
data:
  comments:
    - id: ...
      note_id: ...
      content: ...
      like_count: ...
      liked: false
      ip_location: ...
      create_time: 1757000000000
      is_author: false        # from show_tags
      is_pinned: false        # from show_tags
      sub_comment_count: ...
      sub_comment_cursor: ...
      sub_comment_has_more: false
      user_info: {nickname: ..., user_id: ...}
      at_users: [...]         # nicknames only
      target_comment: {id: ..., nickname: ...}  # only when present
  cursor: ...
  has_more: true
  total_fetched: ...          # only on comments --all
  pages_fetched: ...          # only on comments --all
```

Dropped by the comment projection: `user_info.avatar`, `user_info.xsec_token`, raw `show_tags`, embedded `sub_comments`, `pictures`, `status`, `invalid`.

`comments --limit N` caps the top-level comment count: single-page mode truncates to the first N entries with `cursor` / `has_more` passed through untouched; with `--all` it stops paginating once N comments are fetched and reports the resumable `cursor` / `has_more`.

## Risk state files

Local state files under `~/.xiaohongshu-cli/` (all 0600). All readers must silently degrade on a missing/corrupt file — state files must never break normal requests.

### `risk_marks.json` — resource-level risk marks (shared with cli-gateway)

The gateway (cli-gateway) is the primary writer; the CLI reads it at read-only command entries and also appends self-marks when it triggers a captcha itself.

```json
{
  "version": 1,
  "marks": [
    {
      "kind": "note | keyword | comment",
      "value": "<note_id | keyword | comment_id>",
      "note_id": "<owning note id, optional>",
      "marked_at": 1758000000000,
      "ttl_seconds": 14400,
      "reason": "<error code or free text, e.g. http_461>"
    }
  ]
}
```

Semantics:

- A mark is **active** while `marked_at + ttl_seconds * 1000 > now_ms`; expired marks are ignored (and pruned on the next CLI write).
- Matching: `search` checks `("keyword", <keyword>)`; `read` / `comments` check `("note", <note_id>)`; `sub-comments` checks `("comment", <comment_id>)` and `("note", <note_id>)`.
- An active match fast-fails the command with the `verification_required` error envelope — zero upstream requests.
- CLI self-marks use `ttl_seconds = 14400` (4h, aligned with the gateway's 4h cooldown tier). The CLI dedupes by `(kind, value)` and caps the list at 512 entries.

### `risk_state.json` — account-level backoff state

Written by the CLI on captcha (HTTP 461/471); the gateway may also write `cooldown_until`. Both sides honor the later `cooldown_until`.

```json
{
  "version": 1,
  "captcha_timestamps": [1758000000.0],
  "last_captcha_at": 1758000000.0,
  "captcha_count_24h": 1,
  "cooldown_until": 1758002700.0,
  "delay_multiplier": 2.0,
  "updated_at": 1758000000.0
}
```

- `captcha_timestamps` are epoch seconds pruned to a 24h window; `captcha_count_24h` is derived from them.
- CLI-local cooldown tiers by 24h count: 1 → 45min, 2 → 4h, 3+ → 24h. Authoritative tier escalation stays with the gateway.
- `delay_multiplier` (1.0–8.0, doubling per captcha) is re-applied to the request delay at client start.
- While `cooldown_until` is in the future, all commands fail fast with `verification_required` — zero upstream requests.

### `fingerprint.json` — device fingerprint + signing session (CLI-internal)

```json
{
  "version": 1,
  "created_at": 1758000000.0,
  "fingerprint": {"x1": "...", "x7": "...", "...": "..."},
  "session": {
    "page_load_timestamp": 1758000000000,
    "sequence_value": 123,
    "window_props_length": 45,
    "updated_at": 1758000000.0
  }
}
```

Generated once and reused by every process; rotated on login/cookie refresh or via `xhs fingerprint-reset`.
