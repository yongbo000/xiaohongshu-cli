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
