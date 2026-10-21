# Assumptions and open questions

The implementation follows `AI_AGENT_SPEC.md`. This file records choices made where the spec
was ambiguous, plus product questions that remain open. Update it when a decision changes.

## Verification status

Automated verification completed in this environment:

- `uv sync --all-groups --frozen`
- `uv run ruff check .` and `uv run ruff format --check .`
- `uv run pytest`: 164 tests against PostgreSQL 15 and RustFS
- `uv run alembic upgrade head`, `downgrade base`, and `alembic check`
- `docker compose build bot` and `docker compose config`
- `scripts/backup.sh` against the local Compose services

Not executed here because a real Telegram bot token and test accounts were not available: the
manual end-to-end Telegram smoke test described in `README.md`. The dispatch smoke test exercises
the same handler/middleware path with a fake Telegram transport.

## Product assumptions

1. **One current raffle.** Only one raffle can be `active`/`closed`, enforced by a partial unique
   index. `draft` and `scheduled` raffles may coexist with a current raffle.
2. **BYN only.** Currency is fixed to `BYN`; no conversion or multi-currency support.
3. **Manual payment verification.** There is no bank API or OCR. An organization representative
   verifies recipient, amount, date and status from the receipt.
4. **Payment methods are per organization** and apply to all raffles. Each application stores a
   snapshot of the title and MarkdownV2 instructions.
5. **Minimum amount is per payment**, not cumulative. The cumulative rule applies only to ticket
   calculation across confirmed payments.
6. **Remainders are per participant.** Remainders of different participants are never pooled.
7. **Manual paper draw is official.** The optional random draw is off by default and only
   records a result; it does not replace the paper draw.
8. **Finished means locked.** No confirmations, rejections, voids, printing before `finished` or
   ticket changes after `finished`.
9. **Submitted applications never auto-expire.** Only drafts (`draft`, `awaiting_receipt`) expire
   after 24 hours. Reviewers see pending age and can send a manual reminder.
10. **Receipt storage.** Receipts are uploaded to a private RustFS bucket. PostgreSQL stores only
    metadata and the object key. There is no permanent local receipt directory.
11. **Admin identity.** `ADMIN_IDS` is the source of truth. Admins are synced at startup; other
    roles are preserved. Revoking an admin role through the bot is disabled; change the
    environment instead.
12. **Organizer parity.** Organizers have all admin capabilities except managing organizers.
13. **Invite lifetime.** Invites expire after 7 days, are single-use, and are stored as SHA-256
    hashes. Opening a link first grants the role; the link is sensitive.
14. **Consent.** Rules/privacy acceptance is required before organization buttons. A rules or
    privacy version change invalidates prior consent. Publication of a winner's name is opt-in;
    the default is ticket number only.
15. **Age confirmation.** Controlled by `REQUIRE_AGE_CONFIRMATION` (default `false`). The spec
    mentions age confirmation as conditional but does not define the environment variable.
16. **Draft TTL.** 24 hours from creation, checked by the scheduler. A draft that never reached
    receipt submission cannot submit after acceptance stops.
17. **Receipt uploads use memory buffers.** The Telegram file is downloaded into `BytesIO`,
    validated, uploaded to RustFS and dropped. This avoids temporary local files entirely; the
    spec permits temporary files but does not require them.
18. **Object keys are generated server-side**: `receipts/{raffle_id}/{application_id}/<safe_unique_id>.<ext>`.
    User input never becomes part of a key.
19. **Retention cleanup** deletes RustFS objects after `RECEIPT_RETENTION_DAYS` past
    `finished_at`, then nulls `receipt_object_key` and keeps metadata/audit. The old key is kept
    in the audit payload. No extra schema column was added.
20. **Receipt duplicate detection** uses a global partial unique index on
    `receipt_file_unique_id` (best effort). Re-uploads with a new Telegram ID are caught by
    manual review.
21. **Card delivery retries.** Delivery errors are audited, admins are alerted, and the scheduler
    retries cards with no `card_message_id`. User confirmation notifications are retried through
    `notification_error`/`user_notified_at`.
22. **Receipt fallback.** Cards use Telegram `file_id` first. If Telegram rejects it, the bot
    downloads the object from RustFS and sends it as a file. If both fail, the application stays
    pending and admins are alerted.
23. **Management chat.** `MANAGEMENT_LOG_CHAT_ID` receives a non-actionable copy of each card. It
    is also the fallback review destination when the only reviewer is the participant; if it is
    not configured and no second reviewer exists, the card is not sent and admins are alerted.
24. **Card authorization.** Confirmations are accepted from the chat where the card was sent or
    from an authorized reviewer's private chat (admin panel). Forwarded cards in other group
    chats are rejected.
25. **Two-step confirmation** is on by default. The second step revalidates all data; the final
    transaction is authoritative.
26. **Participant totals rows are created when a draft starts.** This avoids a PostgreSQL
    foreign-key key-share lock upgrade during concurrent confirmations (application →
    participant totals → raffle lock order). Confirmation still does `INSERT ... ON CONFLICT DO
    NOTHING` before locking, as required.
27. **Public application IDs** are generated by a PostgreSQL sequence:
    `A-<year>-<six digits>`, e.g. `A-2026-000123`.
28. **Payment codes** are exactly five characters from an alphabet excluding `0/O` and `1/I`.
    Codes are unique per raffle and never reused.
29. **Random draw algorithm** chooses uniformly among tickets still below the
    `ceil(prizes_count / last_ticket_number)` cap. Existing winners block a repeat draw; an
    organizer can reset winners explicitly (audited). Manual result recording can overwrite a
    place.
30. **Winner announcements** are not generated automatically. The bot records winners and
    allows admins/organizers to inspect participants. Publication is a manual action; personal
    data is shown only with the participant's separate consent.
31. **Exports.** CSV contains the Applications columns (UTF-8 BOM, `;` separator). XLSX contains
    Applications, Participants, Tickets and Summary. Org representatives get no Participants
    sheet and only their organization's rows. CSV of participants is not a separate format in
    MVP. Files over 45 MB are refused rather than split.
32. **Role assignment by forwarded message** is supported inside the add-role wizard: forward
    the user's message instead of entering an ID. A Telegram ID can also create a stub user row
    even if the person has not started the bot yet; notifications are best effort.
33. **Logo field.** Organizations have `logo_file_id`, but the MVP admin editor does not expose
    logo upload. This is an optional field and does not affect activation.
34. **PDF tickets** use DejaVu Sans, two columns × five rows per A4 page, no names,
    organizations or personal data. The PDF is refused before `finished` and when there are no
    tickets. Font path can be set with `DEJAVU_FONT_PATH`.
35. **Payment method editor** accepts raw MarkdownV2 and validates it before saving. Formatting
    help is provided; plain text must escape reserved characters. The editor does not silently
    rewrite admin input, to avoid corrupting copyable values.
36. **Raffle descriptions and rules** are plain text and are escaped for MarkdownV2 when shown.
    Payment instructions are the only admin field rendered as MarkdownV2.
37. **Slugs** are generated from titles (Cyrillic transliteration) and remain stable after
    creation, so export filenames do not change when a title is edited. Slugs must be unique.
38. **Scheduled activation is revalidated.** A scheduled raffle that cannot start (another
    current raffle, missing organization chat, no active payment method) stays `scheduled` and
    admins are notified.
39. **Editing locked fields.** Ticket price, minimum amount, prize count, start date and the
    organization set are locked after activation. With zero applications, the raffle can be
    returned to `draft` and reactivated. `end_at` may be extended until `finished`; extending a
    `closed` raffle with a future date returns it to `active`.
40. **Delete** is allowed only for a `draft` with no applications.
41. **Draft replacement.** A user has at most one draft per raffle. Starting a new donation
    offers Continue / Start over / Cancel. Cancellation is atomic and fails if a reviewer has
    already confirmed. A partial unique index on `(raffle_id, user_id)` for `draft` and
    `awaiting_receipt` makes this race-safe; an integrity violation is returned as a friendly
    "draft already exists" error.
42. **Rate limiting** uses in-process memory (single instance). Limits reset on restart; database
    constraints still enforce the 20-applications-per-raffle limit.
43. **Webhook mode** is optional. When `WEBHOOK_URL` is set, the bot serves the webhook on
    `WEBHOOK_PORT` (default 8443) at `WEBHOOK_PATH`; otherwise it uses long polling.
44. **Health endpoint** is a minimal HTTP server on `HEALTH_PORT` (default 8081). It returns
    `200` only when PostgreSQL answers `SELECT 1` and RustFS responds to a bucket head request;
    otherwise it returns `503`.
45. **Testing database** is separate (`raffle_test`) and recreated at pytest session start via
    Alembic. There is no SQLite fallback.
46. **No audit-log UI filters in MVP.** The panel shows the latest 20 entries and exports the
    full log as CSV. Filtering is possible in the CSV.
47. **Receipt ZIP archive** from the spec's phase 2 is not implemented.
48. **Blocking users.** `users.is_blocked` prevents new drafts and blocks existing
    draft/receipt/amount flows. There is no admin UI toggle in MVP; support can set it directly
    with a reviewed migration/script.

## Open questions for the product owner

These do not block implementation; the safest defaults above are in use.

1. Organization list, descriptions, review chats and payment method texts.
2. Review chat structure: one chat per organization or one forum with topics.
3. Channel URL and whether subscription should ever be checked (currently a soft reminder only).
4. Print ticket layout: tickets per A4, logos, QR codes, signatures, perforation.
5. Expected load and deployment target; RustFS single-node vs cluster, TLS and backup policy.
6. Privacy policy, rules URL, age restriction and legal review.
7. Support contact and response expectations.
8. Whether to add the receipt ZIP archive in MVP (currently out of scope).
9. Whether winner recording and announcement generation belong in the bot (recording is
   implemented; publication is manual).
10. Whether admins/organizers may correct an application amount from the receipt (not supported;
    safest default is to void and re-submit, but void is only for the last range).
11. Whether an admin/organizer should be able to force-confirm a payment without a receipt.
12. Exact retention period required by Belarusian accounting rules (`RECEIPT_RETENTION_DAYS`
    defaults to 90; PostgreSQL metadata is retained indefinitely).

## Legal

This software is not legal advice. Before launch, a Belarusian lawyer should review raffle
rules, charity/tax requirements, personal data processing, age restrictions, prize fulfillment
and taxation. The bot does not publish results automatically.

49. **Displayed amounts omit insignificant trailing zeros** (`5`, `15,5`) per product owner request. XLSX/CSV exports keep two decimals (`5.00`) for spreadsheet consistency.

50. **User cancellation deletes the application.** Cancelling an unprocessed application removes its row instead of keeping a `cancelled` status. The audit log keeps `application_cancelled`, the public identifier and the old status with `application_id = NULL`; the review card is edited to "Cancelled" and the RustFS receipt object is removed.
