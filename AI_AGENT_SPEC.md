# AI Agent Build Specification

## Telegram Charity Raffle Bot (BYN)

**Document language:** English  
**Product UI language:** Russian  
**Status:** implementation contract  
**Stack:** Python 3.11+, aiogram 3.x, SQLAlchemy 2.x, PostgreSQL 15+, RustFS (S3), uv  
**Money:** BYN only

---

## 0. Instructions for the AI agent

You are implementing the product described in this document. Treat this file as the build contract.

### 0.1. How to work

1. Read the entire document before writing code.
2. Produce a short implementation plan and a task checklist before coding.
3. Implement in small, testable increments. Run tests after each increment.
4. Keep all code, comments, identifiers and technical docs in English.
5. Keep all user-facing bot copy in Russian.
6. Do not invent product behavior. If something is ambiguous and not blocking, choose the safest option, document the assumption in `ASSUMPTIONS.md`, and continue.
7. Ask a question only when the ambiguity blocks implementation or could cause data loss, financial mistakes, or a privacy/legal issue.
8. Never commit secrets, `.env`, tokens, receipt files, or production data.
9. Never run destructive database commands against a non-test database.
10. Add tests for every invariant and financial operation.
11. Do not use HTML parse mode anywhere. Use Telegram MarkdownV2.
12. Do not add the removed `org_viewer` role, do not hardcode organizations, and do not add structured payment fields to organizations.
13. Use **uv** for Python dependency management and for running every Python command. Commit `pyproject.toml`, `uv.lock` and `.python-version`. Do not add `requirements.txt`, Poetry, Pipenv or bare `pip install` instructions.
14. Use **PostgreSQL 15+ in every environment**, including local development and tests. There is no SQLite fallback. Use `asyncpg` as the async driver.
15. Store receipt binaries in **RustFS** (S3-compatible object storage). PostgreSQL stores metadata only, never image/PDF blobs. Do not keep a permanent local receipt directory; use only temporary files during upload.

### 0.2. Definition of done for the whole task

The task is done only when:

- All acceptance criteria in section 16 pass.
- `uv sync --all-groups` completes.
- `uv run pytest` passes against PostgreSQL.
- `uv run ruff check .` passes.
- `uv run alembic upgrade head` applies cleanly to an empty PostgreSQL database.
- `docker compose up` starts the bot, PostgreSQL and RustFS.
- `.env.example` exists and documents every variable.
- `README.md` explains local setup with `uv`, PostgreSQL, RustFS, migrations, tests, and deployment.
- A manual smoke test with a Telegram test bot passes the full flow.
- The concurrency test for two simultaneous confirmations of the same user passes.
- The MarkdownV2 escaping tests pass.
- No secrets or receipt files are committed.
- The agent has documented all assumptions and unresolved questions.

---

## 1. Product summary

The bot runs charity raffles. A participant chooses an organization, chooses one of that organization's payment methods, receives payment instructions, enters the amount, and uploads a receipt. A representative of that organization verifies the payment in a private chat. Only after confirmation does the bot issue ticket numbers.

Money goes directly to the organizations. The bot never accepts, holds, or transfers money.

### 1.1. Core product rule

The organization affects only:

- where the money goes;
- who verifies the receipt.

It does **not** affect:

- the number of tickets;
- the prize pool;
- the chance to win.

All confirmed donations of one participant within one raffle are summed, regardless of organization or payment method.

### 1.2. Out of scope for MVP

- Payment processing or bank API integration.
- Automatic bank-transfer verification.
- Subscription gating (subscription is only a soft reminder).
- Multi-currency.
- Web admin panel.
- OCR as a replacement for manual verification.
- Per-raffle payment methods.
- Automatic expiry of submitted applications.
- The `org_viewer` role.

---

## 2. Non-negotiable invariants

These rules must be enforced in code, database constraints where practical, and tests.

1. Ticket numbers are issued only after payment confirmation.
2. Tickets are calculated from the participant's total confirmed amount within one raffle, across all organizations and payment methods.
3. `tickets = floor(confirmed_amount / ticket_price)`.
4. When an application is confirmed, issue only the delta:
   `delta = floor((current_confirmed_amount + application.amount) / ticket_price) - current_tickets_issued`.
5. Rounding remainders are per participant. Remainders of different participants are never pooled.
6. A participant's ticket numbers may be non-contiguous. The raffle's global numbering must be contiguous: `1..last_ticket_number`.
7. `last_ticket_number = sum(tickets_awarded for confirmed applications) = sum(participant_totals.tickets_issued)`.
8. A void is allowed only for the last issued range and only while the raffle is `active` or `closed`. After `finished`, void is forbidden.
9. Printing is allowed only after `finished`. Before `finished`, the print PDF must not be generated.
10. The minimum amount is **5 BYN per individual payment**. The cumulative ticket calculation still applies.
11. Payment methods are free-form MarkdownV2 text. There are no structured `iban`, `card_number`, `recipient_name`, `bank_name` or `payment_purpose` fields.
12. No HTML. All bot messages and admin-entered payment instructions use Telegram MarkdownV2.
13. A reviewer cannot confirm their own application.
14. Submitted applications are never auto-expired. Only drafts may expire automatically.
15. At most one raffle is `active` or `closed` at a time.
16. The bot must be idempotent: repeated button presses must not issue duplicate tickets.
17. Money is stored and calculated with `Decimal`, never `float`.
18. Receipt binaries are stored in a private RustFS bucket. PostgreSQL stores metadata and `receipt_object_key` only; there is no permanent local receipt directory.

---

## 3. Roles and permissions

### 3.1. Roles

| Role | Who | How assigned | Scope |
|---|---|---|---|
| `participant` | Any Telegram user | Automatically on first `/start` | Own applications only |
| `org_rep` | Organization employee/volunteer | Admin or organizer, by Telegram ID, forwarded message, or one-time invite | One organization only |
| `organizer` | Trusted person | Admin only, by Telegram ID, forwarded message, or one-time invite | All organizations and raffles; everything except managing organizers |
| `admin` | Bot owner | `ADMIN_IDS` environment variable | Everything, including assigning/removing organizers |

There is no `org_viewer` role.

### 3.2. Permission matrix

| Action | participant | `org_rep` | `organizer` | `admin` |
|---|---:|---:|---:|---:|
| Participate in raffles | yes | yes | yes | yes |
| View own applications and tickets | yes | yes | yes | yes |
| View applications of own organization | no | yes | yes | yes |
| Confirm/reject own organization's payments | no | yes | yes | yes |
| Confirm/reject any organization's payments | no | no | yes | yes |
| Create/edit raffles | no | no | yes | yes |
| Stop/resume acceptance | no | no | yes | yes |
| Finish/archive raffle | no | no | yes | yes |
| Manage organizations and payment methods | no | no | yes | yes |
| Export own organization | no | yes | yes | yes |
| Export all organizations | no | no | yes | yes |
| Manage organization representatives | no | no | yes | yes |
| Manage organizers | no | no | no | yes |
| Change bot settings | no | no | yes | yes |
| View audit log | no | no | yes | yes |
| Void a confirmation | no | no | yes | yes |

The organizer has the same capabilities as the admin, including finishing/archiving, settings, organizations, exports, audit and void. The only exception is assigning or removing organizers: only the admin can do that.

### 3.3. Role assignment rules

- `admin` is defined by `ADMIN_IDS` in the environment. The environment list is the source of truth.
- On startup, sync `admin` roles: add missing IDs, revoke `admin` from users no longer in the list. Keep their other roles.
- The bot must not start if `ADMIN_IDS` is empty.
- `admin` cannot be assigned through the bot or an invite.
- `organizer` is assigned only by an admin. Organizers cannot assign or revoke organizers.
- `org_rep` and `organizer` can be assigned by an admin. `org_rep` can also be assigned by an organizer.
- An `org_rep` role must have `organization_id`.
- A user may have multiple roles; permissions are the union.
- Role revocation takes effect immediately.
- Every role grant/revocation is written to the audit log.
- The bot notifies the affected user when a role is granted or revoked.
- The last admin cannot be removed by the bot.

### 3.4. Invites

- Format: `https://t.me/<bot_username>?start=inv_<token>`.
- Invite roles: `organizer` or `org_rep` only.
- `organization_id` is required for `org_rep`.
- Token is one-time, stored as a hash, expires after 7 days by default.
- A used, expired, or revoked invite returns a clear error.
- An unused invite can be revoked.
- Invite processing happens before the active-raffle check, so a role can be obtained even when no raffle is running.
- Invite links are sensitive; warn that whoever opens first gets the role.

### 3.5. Onboarding an organization representative

1. The organization names one or more reviewers.
2. Admin or organizer creates an `org_rep` invite for that organization.
3. The person opens the invite and gets the role.
4. A Telegram chat administrator adds the person to the organization's review chat.
5. The person opens `/admin` and sees only that organization's applications.

If the person is not in the review chat, they can still work through `/admin`, but they will not receive cards in the chat.

---

## 4. Domain model and terminology

- **Raffle** — event with a title, ticket price, dates, organizations, prize count, status, and its own ticket counter.
- **Application** — one donation by one user to one organization.
- **Ticket** — integer number issued for confirmed donations.
- **Participant total** — confirmed amount and issued tickets for one user in one raffle.
- **Payment method** — free-form MarkdownV2 instructions belonging to an organization. An organization may have many.
- **RustFS** — private S3-compatible object storage for receipts. PostgreSQL stores metadata and the object key only.
- **Confirmation** — authorized action that issues new tickets.
- **Void** — cancellation of a confirmation under strict conditions.
- **Current raffle** — the single raffle in `active` or `closed`.
- **Prize count** — `prizes_count`, the number of prizes/winners.
- **Max prizes per ticket** — `ceil(prizes_count / last_ticket_number)`; applies only to bot-assisted random draw.

---

## 5. Raffle lifecycle

### 5.1. Statuses

| Status | Meaning | New applications | Confirmations |
|---|---|---|---|
| `draft` | Being configured | no | no |
| `scheduled` | Waiting for `start_at` | no | no |
| `active` | Accepting applications | yes | yes |
| `closed` | Acceptance stopped, review ongoing | no | yes, for applications submitted before the stop |
| `finished` | All tickets confirmed and locked; printing and paper draw allowed; void forbidden | no | no |
| `archived` | Results published, hidden from participant UI | no | no |

### 5.2. One current raffle

- Only one raffle may be `active` or `closed`.
- A new raffle can be `draft` or `scheduled` while another is current, but cannot be activated until the previous one is `finished` or `archived`.
- If a scheduled raffle reaches `start_at` while another is current, it stays `scheduled` and the admin is notified.

### 5.3. Creation and editing

Create via `/admin` → Raffles → Create:

1. Title.
2. Description and rules.
3. Ticket price in BYN, default 5.00.
4. Minimum amount per payment, default equal to ticket price.
5. `prizes_count`, default 1.
6. Start date/time.
7. End date/time.
8. Channel URL and title.
9. Organizations, default all active.
10. Confirm and save.

Rules:

- Duplicate raffle copies settings, resets counter and dates.
- Delete is allowed only for a `draft` with no applications; admin or organizer.
- After activation, these fields are locked: ticket price, minimum amount, `prizes_count`, currency, start date, organization set.
- If there are no applications, an admin may return the raffle to `draft`, change locked fields, and reactivate. Audit the change.
- `end_at` may be extended while the raffle is not `finished`. Extending a `closed` raffle returns it to `active`.
- After `finished`, extension and reopening are forbidden.

### 5.4. Activation checks

Before activation:

- no other current raffle;
- `ticket_price > 0`;
- `min_amount >= ticket_price`;
- `prizes_count >= 1`;
- `end_at > start_at`;
- at least one organization;
- every selected organization has a name, review chat, and at least one active payment method.

### 5.5. Acceptance stop

Two mechanisms:

1. Manual "Stop acceptance" button with confirmation.
2. Automatic deadline at `end_at`.

Rules:

- Scheduler sets `closed` at `end_at`.
- Every participant action also checks `now >= end_at`, so a lagging scheduler cannot accept a late application.
- On startup, catch up overdue deadlines.
- Stop does not cancel submitted applications; they can still be confirmed.
- Drafts that never reached receipt submission are expired and cannot submit a receipt.
- After the stop, new users see: "Приём заявок завершён! Розыгрыш закрыт, ожидайте результатов на канале".
- Any new participant action except "My applications" gets the closed message and the unfinished state is cleared.
- Resume is possible if not `finished` and `end_at` is in the future, or after extending the date.

### 5.6. Finish

- Admin or organizer.
- Available from `closed`. If `active`, the bot first offers to stop acceptance.
- Before finishing, no `pending_review` or `revision_requested` applications may remain.
- If they remain, the bot shows the count and offers:
  - process them first;
  - or explicitly move them to `expired` with user notification and finish.
- Drafts are moved to `expired` at finish.
- After `finished`: no new confirmations, no ticket changes, no voids.
- Before `finished`: print PDF is forbidden.
- After `finished`: print PDF is allowed.
- Finishing does not select winners; it locks tickets.
- Audit the finish.

---

## 6. Organizations and payment methods

### 6.1. Organizations

Organizations are configured in the admin panel. There is no fixed count and no hardcoded organization list.

Fields:

- display name;
- slug (unique);
- short description;
- logo (optional);
- `review_chat_id`;
- optional `review_thread_id`;
- contact URL;
- active flag;
- raffles the organization participates in;
- payment methods.

Do not store payment data on the organization.

### 6.2. Payment methods

One organization may have multiple payment methods. Examples: "Card BYN", "ERIP", "IBAN".

Fields:

- title, max 64 characters;
- `instructions_text`, MarkdownV2, max 3000 characters;
- active flag;
- sort order.

The instructions are free-form text with no structured bank fields. Example final MarkdownV2:

```text
Переведите на карту:
`1234 5678 9012 3456`

*Получатель:* Иван Иванов
*Назначение:* пожертвование на уставную деятельность
```

Rules:

- Copyable values go in backticks. Telegram renders them as code and allows tap-to-copy.
- Supported formatting: `*bold*`, `_italic_`, `__underline__`, `~strikethrough~`, `` `code` ``, `[text](url)`.
- No HTML.
- The admin editor escapes plain-text special characters; formatting buttons insert markup.
- The editor shows a preview.
- The bot validates MarkdownV2 before saving.
- If Telegram rejects markup at send time, send the same text without markup and notify the admin.
- Payment methods belong to the organization and apply to all its raffles. Per-raffle payment methods are not supported in MVP.
- Editing a payment method does not change existing applications; each application stores a snapshot of the title and instructions.
- A disabled method is hidden from new participants but remains in application history.
- An organization without an active payment method cannot be activated in a raffle.

### 6.3. Organization selection

- Show only organizations enabled for the current raffle.
- Randomize the order per user:
  `seed = stable_hash(user_id + raffle_id)`.
- The order is stable for one user within the raffle and different across users.
- If only one organization is enabled, show one button without randomization.
- After organization selection:
  - one active payment method → show instructions immediately;
  - multiple → show method buttons.
- Save the selected organization and method in the draft.
- Before payment, the user may go back and change organization or method.
- After payment, changing organization is forbidden; support handles mistakes.

### 6.4. Payment code

- Generate a unique code when the draft is created, for example `K7M2Q`.
- Exactly 5 characters.
- Alphabet excludes `0/O` and `1/I`.
- Unique within the raffle.
- Show it in the payment instructions and the admin card.
- Ask the participant to add it to the bank payment purpose if the bank allows.
- The code is not a secret and does not replace receipt verification.
- Changing payment method does not change the code. Changing organization creates a new draft and code.

---

## 7. Participant flow

### 7.1. `/start`

1. Upsert the user: Telegram ID, username, first/last name, language, last seen.
2. If no current raffle: show the no-active-raffle message and channel button.
3. If the raffle is scheduled: show the start date.
4. If the raffle is closed: show the closed message and "My applications".
5. If active: show greeting, rules summary, ticket price, prize count, deadline, randomized organization buttons, and "My applications".
6. If the user has an unfinished draft: offer Continue / Start over / Cancel draft.
7. If the user has a pending application: show its status and allow a new application with a warning that it is a separate donation and separate receipt.

Before first participation, show a consent screen:

- link to raffle rules;
- link to privacy policy;
- "I accept the rules and privacy policy" button;
- separate age confirmation if required (e.g., "I am 18+");
- separate optional consent to publish the name if the user wins; default is ticket number only.

Without rules and privacy acceptance, organization buttons are hidden. Store consent facts and document versions. If the rules version changes, ask again.

### 7.2. Select organization

- Validate the raffle is active, the organization is enabled, it has an active payment method, and the user is not blocked.
- Create a `draft` application.
- Go to payment-method selection or show the single method.
- Buttons: Back, Cancel.

### 7.3. Select payment method

- If multiple active methods, show inline buttons with titles.
- Save `payment_method_id`, title snapshot, and instructions snapshot.
- Show instructions, payment code, and amount prompt.
- Buttons: Back to methods, Cancel.
- If one method, skip the step but still store it.

### 7.4. Enter amount

Parse a number with a dot or comma, trimming surrounding spaces. Use `Decimal`.

Reject:

- non-number, `NaN`, `Infinity`;
- amount below `min_amount`;
- zero or negative;
- amount above `MAX_DONATION`;
- more than two decimal places.

On error, stay in the amount state, keep organization and payment method, and wait for a new input. Do not reset the draft.

Preview:

```text
current_amount = participant_totals.confirmed_amount (0 if absent)
current_tickets = participant_totals.tickets_issued (0 if absent)
preview_amount = current_amount + entered_amount
preview_tickets = floor(preview_amount / ticket_price)
preview_delta = preview_tickets - current_tickets
```

Show:

- total amount after this payment;
- total tickets after this payment;
- how many new tickets this payment would add.

Example, ticket price 5 BYN:

- 9 BYN = 1 ticket, 4 BYN remainder.
- After 9 BYN, a 5 BYN payment → total 14 BYN = 2 tickets (+1).
- After 9 BYN, a 6 BYN payment → total 15 BYN = 3 tickets (+2).
- The bot may suggest 6 BYN to use the remainder fully, but the minimum payment is still 5 BYN.

After valid input:

- status becomes `awaiting_receipt`;
- ask for receipt;
- buttons: Change amount, Cancel.

If the raffle closes while the user is entering the amount, show the closed message and do not create an application.

### 7.5. Receipt upload

Accept:

- Telegram `photo` (take the largest size);
- PDF document: MIME `application/pdf`, extension `.pdf`, magic bytes `%PDF-`;
- image document: JPEG or PNG, MIME `image/jpeg` / `image/png`, valid magic bytes.

Reject:

- text, stickers, voice, video, contacts, location;
- other document types;
- files larger than `RECEIPT_MAX_MB` (default 20 MB);
- corrupted PDF/images;
- password-protected PDFs (ask to re-save without a password).

Receipt rules:

- one receipt per application;
- if a photo album is sent, ask for one receipt in one message;
- duplicate detection via global `file_unique_id` (best effort); manual review catches re-uploads with a new ID;
- download the file from Telegram to a temporary file and upload it to a private RustFS bucket;
- store `file_id`, `file_unique_id`, `receipt_object_key`, `receipt_etag`, MIME and size in PostgreSQL; never store binaries in the database;
- object key: `receipts/{raffle_id}/{application_id}/{file_unique_id}.{ext}`; do not build keys from user input;
- use `file_id` for the admin card; if it no longer works, download the object from RustFS and send it;
- if Telegram download or RustFS upload fails, do not move to `pending_review`; ask the user to retry and alert the admin after repeated failures;
- delete the temporary file immediately after a successful RustFS upload; there is no permanent local receipt directory;
- receipt must show recipient, amount, date and status; the organization representative verifies this.

After successful upload:

- status `pending_review`;
- tell the user tickets are calculated from the total confirmed amount and numbers are issued only after confirmation;
- send the card to the organization's review chat.

### 7.6. Application statuses

| Status | Meaning |
|---|---|
| `draft` | Organization selected, method or amount not complete |
| `awaiting_receipt` | Amount entered, waiting for receipt |
| `pending_review` | Receipt received, waiting for review |
| `revision_requested` | Reviewer requested a new receipt |
| `confirmed` | Payment confirmed, new tickets issued |
| `rejected` | Final rejection, no tickets |
| `cancelled` | Legacy user cancellation; new cancellations delete the application |
| `expired` | Not processed before raffle finish, or draft expired |
| `voided` | Confirmation cancelled under the void rules |

### 7.7. My applications

`/my` or "My applications":

- top summary for the current raffle: confirmed amount, total tickets, all ticket ranges;
- list of applications: raffle, organization, payment method, amount, status, date, tickets added;
- `pending_review`: "Under review";
- `confirmed`: new ranges for that application plus all current ranges;
- `rejected`: reason and support contact;
- `revision_requested`: button to upload a new receipt;
- "Send another application" if the raffle is active.

A user may cancel `draft`, `awaiting_receipt`, `pending_review`, or `revision_requested` if not processed. Cancellation is atomic: if a reviewer already confirmed, cancellation fails and the current status is shown. On cancellation, delete the application row (audit log keeps the public identifier and old status), edit the organization card to "Cancelled", disable its buttons, and delete the stored receipt object.

### 7.8. Multiple payments and cumulative tickets

- A user may make multiple donations in one raffle, including to different organizations and via different methods.
- Each donation is a separate application with its own receipt, payment method and payment code.
- All confirmed applications of the user in the raffle are summed.
- Total tickets = `floor(total confirmed amount / ticket_price)`.
- On each confirmation, issue only the difference between the new total and the tickets already issued.
- Organization does not affect tickets or prizes.
- Remainders are per participant and are not pooled across participants.
- Minimum amount is checked per individual payment.

Examples:

| Step | Action | Confirmed total | Total tickets | New numbers |
|---|---|---:|---:|---|
| 1 | 9 BYN to Organization A | 9 BYN | 1 | 1 |
| 2 | 6 BYN to Organization B | 15 BYN | 3 | 2–3 |
| 3 | 5 BYN to Organization A | 20 BYN | 4 | 4 |

Remainders are not pooled:

- User A donates 9 BYN → 1 ticket, 4 BYN remainder.
- User B donates 9 BYN → 1 ticket, 4 BYN remainder.
- Total collected is 18 BYN, but there are 2 tickets, not 3.

A participant's numbers may be non-contiguous:

- User A gets number 1.
- User B gets number 2.
- User A gets numbers 3–4.
- User A's final numbers: `1, 3–4`.

Therefore the total number of tickets in a raffle may be less than `floor(total collected / ticket_price)`. This must be stated in the rules.

### 7.9. Several applications

- Multiple applications per user are allowed.
- Each has its own receipt, method, code and new ranges.
- Applications are not merged, but their amounts are summed for tickets.
- Only one draft at a time; starting a new one offers to continue or replace the draft.
- Drafts older than 24 hours become `expired`. The payment code is not reused. If the user paid late, support handles it manually.

---

## 8. Review and confirmation

### 8.1. Card routing

- Send the application card to the selected organization's review chat.
- Recommended: one private chat per organization.
- Alternative: one private forum chat with one topic per organization (`review_thread_id`).
- Optional management chat (`MANAGEMENT_LOG_CHAT_ID`) receives a non-actionable copy for oversight and is used for fallback.
- The bot must be able to send messages in the chat; admin rights are recommended for pinning/deleting.
- If sending fails, retry with backoff and notify the admin. The application stays `pending_review`.

### 8.2. Card format

Photo → photo with caption. PDF or image document → document with caption.

```text
📥 *Новая заявка на розыгрыш*

🆔 Заявка: `A-2026-000123`
🔖 Код платежа: `K7M2Q`
👤 Участник: @username (Имя, ID: 123456789)
🏢 Организация: *Организация A*
💳 Способ оплаты: Карта BYN
💰 Сумма: *6,00 BYN*
📊 Уже подтверждено у участника: 9,00 BYN
🎫 Предварительно: +2 билета (всего 3)
📅 Дата: 21.10.2026 14:35
🎗 Розыгрыш: Осень 2026
```

Buttons: `✅ Подтвердить`, `❌ Отклонить`.

If no username: `Имя Фамилия (ID: 123456789)`.

The preview is computed from current confirmed total plus this application amount. It may change if another application of the same user is confirmed concurrently. The final transaction is authoritative.

Before confirmation, show ticket count, not ranges. After confirmation, edit the card with status, actual range, reviewer and time; remove buttons. After rejection, edit with reason, reviewer and time; remove buttons.

Caption limit is 1024 characters. If data does not fit, send the file without caption and a separate text message with details and buttons.

### 8.3. Confirmation

Self-confirmation ban: a reviewer cannot confirm their own application. This applies to all roles, including admin and organizer.

Two-step confirmation:

1. Reviewer presses `✅ Подтвердить`.
2. Bot verifies role/status and shows:
   > "Подтвердить оплату по заявке A-2026-000123? Сумма 6,00 BYN. С учётом предыдущих подтверждённых платежей участника будет начислено: +2 билета (всего 3). Итог пересчитывается в момент подтверждения."
   Buttons: `✅ Да, выдать номера`, `❌ Отмена`.
3. Reviewer presses `✅ Да, выдать номера`.

`CONFIRM_TWO_STEP=false` can disable the second step, but default is true.

If the only reviewer for the organization is the participant, the card goes to the management chat (`MANAGEMENT_LOG_CHAT_ID`), where an admin or organizer can confirm it. If the only admin also participates and there is no organizer, a second admin/organizer is required. The bot warns if no fallback reviewer is available and never allows self-confirmation. If the management chat is not configured, the bot explains that a second reviewer is required.

### 8.4. Confirmation transaction

Lock order for all financial operations:

1. application row;
2. participant totals row;
3. raffle row.

Pseudocode:

```sql
BEGIN;

SELECT id, status
FROM applications
WHERE id = :application_id
FOR UPDATE;

-- verify status = 'pending_review'
-- verify raffle status IN ('active','closed')
-- verify submitted_at <= raffle.end_at
-- verify reviewer role/org scope
-- verify reviewer_id != application.user_id

INSERT INTO participant_totals (raffle_id, user_id, confirmed_amount, tickets_issued)
VALUES (:raffle_id, :user_id, 0, 0)
ON CONFLICT (raffle_id, user_id) DO NOTHING;

SELECT confirmed_amount, tickets_issued
FROM participant_totals
WHERE raffle_id = :raffle_id AND user_id = :user_id
FOR UPDATE;

-- new_amount = confirmed_amount + :amount
-- new_tickets = floor(new_amount / :ticket_price)
-- delta = new_tickets - tickets_issued
-- require delta >= 1

UPDATE raffles
SET last_ticket_number = last_ticket_number + :delta
WHERE id = :raffle_id
  AND status IN ('active', 'closed')
RETURNING last_ticket_number;

-- if no row, rollback
-- first = returned_last - delta + 1
-- last = returned_last

UPDATE participant_totals
SET confirmed_amount = :new_amount,
    tickets_issued = :new_tickets,
    updated_at = now()
WHERE raffle_id = :raffle_id AND user_id = :user_id;

UPDATE applications
SET status = 'confirmed',
    tickets_awarded = :delta,
    tickets_total_after = :new_tickets,
    first_ticket_number = :first,
    last_ticket_number = :last,
    reviewed_by = :reviewer_id,
    reviewed_at = now()
WHERE id = :application_id
  AND status = 'pending_review';

-- if no row, rollback: another reviewer processed it

INSERT INTO audit_log (...);

COMMIT;
```

After commit:

- edit the card;
- send the user a message with new ranges, total amount, total tickets and all current ranges;
- if sending fails, keep the tickets and retry in the background; store `user_notified_at` and `notification_error`.

Concurrency requirements:

- Two reviewers pressing at once: only one transaction succeeds; the other gets "already processed".
- Two payments of the same user in different organizations: the participant totals lock serializes them; no duplicate or missing tickets.
- Two payments of different users: the raffle counter update serializes; ranges are contiguous.

PostgreSQL requirements:

- PostgreSQL 15+ is required in every environment: local development, tests, staging, production.
- Driver: `asyncpg` via SQLAlchemy 2.x async.
- Use `SELECT ... FOR UPDATE` for the application, participant totals and raffle row.
- Use `INSERT ... ON CONFLICT (raffle_id, user_id) DO NOTHING` before locking participant totals.
- Use `UPDATE ... RETURNING` for the raffle counter.
- Default isolation level `READ COMMITTED` is sufficient because row locks serialize financial writes.
- Integration and concurrency tests must run against a real PostgreSQL instance, not a substitute database.
- There is no SQLite fallback.

### 8.5. Rejection and revision

Reject reasons:

- unreadable receipt;
- payment not found;
- amount mismatch;
- wrong organization;
- duplicate receipt;
- other (free text).

Outcomes:

- **Request new receipt** → `revision_requested`; no tickets. Old card is marked "new receipt requested" and buttons removed. User gets "Upload new receipt". After upload, status returns to `pending_review`; a new card with the new file is sent. Allowed while `active` or `closed`; after `finished`, only final rejection. Preserve original `submitted_at`; new upload time is audited.
- **Final rejection** → `rejected`; no tickets; participant totals and counter unchanged. Notify the user with reason and support contact.

### 8.6. Numbering rules

- Numbering is per raffle, shared by all organizations.
- A new raffle starts at 1.
- Numbers only after confirmation.
- Each confirmed application receives a continuous range equal to its ticket delta.
- `delta = floor((previous_confirmed_amount + application_amount) / ticket_price) - tickets_issued`.
- Pending, rejected, cancelled and expired applications consume no numbers.
- Invariant: issued numbers are exactly `1..last_ticket_number`, no gaps.
- `last_ticket_number` equals the sum of `tickets_awarded` over confirmed applications.
- Maximum number equals total issued tickets.
- A participant's numbers may be non-contiguous.
- Remainders are not pooled across participants.

Example:

| Application | User | Organization | Amount | User total | New tickets | New numbers |
|---|---|---|---:|---:|---:|---|
| A | User 1 | Org A | 9 BYN | 9 BYN | 1 | 1 |
| B | User 2 | Org B | 5 BYN | 5 BYN | 1 | 2 |
| C | User 1 | Org B | 6 BYN | 15 BYN | 2 | 3–4 |
| D | User 2 | Org A | 10 BYN | 15 BYN | 2 | 5–6 |

User 1 final numbers: `1, 3–4`; User 2: `2, 5–6`.

### 8.7. Void

Allowed only when:

- application is `confirmed`;
- raffle is `active` or `closed`;
- application's active range is the last issued range:
  `application.last_ticket_number == raffle.last_ticket_number`;
- no confirmations occurred after it.

Void transaction:

1. Lock application, participant totals, raffle.
2. Move the active range to `voided_first_ticket_number` / `voided_last_ticket_number`.
3. Null `first_ticket_number` / `last_ticket_number` so the numbers can be reused.
4. Set status `voided`.
5. Subtract application amount from participant totals.
6. Recompute tickets.
7. Verify `tickets_to_remove == application.tickets_awarded`; otherwise abort.
8. Decrement `last_ticket_number` by removed tickets.
9. Update participant totals.
10. Audit old range and reason.
11. Notify user; edit card: "Подтверждение аннулировано, номера …–…".

After `finished`, void is forbidden. If paper tickets were printed, they must be withdrawn manually. Because printing is allowed only after `finished`, a normal void happens before any paper tickets exist.

### 8.8. Integrity check

The admin panel must verify:

- `raffle.last_ticket_number == sum(tickets_awarded for confirmed applications)`;
- `raffle.last_ticket_number == sum(participant_totals.tickets_issued)`;
- per participant:
  `tickets_issued == floor(confirmed_amount / ticket_price)`;
- `confirmed_amount == sum(amount for confirmed applications)`;
- no gaps or overlaps in active ranges;
- `last - first + 1 == tickets_awarded` for every confirmed application;
- voided applications have null active ranges and filled `voided_*`;
- no confirmed application has missing ranges.

On mismatch, show a report and alert the admin. New confirmations may be paused until resolved.

---

## 9. Prizes and winner selection

### 9.1. Prize count

Every raffle has `prizes_count`, the number of prizes and winners. It is fixed at activation.

- Manual paper draw: the organizer decides the order and may allow one ticket to win multiple prizes. The bot does not enforce a per-ticket cap. It only validates that the ticket exists and the place is in `1..prizes_count`.
- If the organizer wants a different prize count, change it before activation.
- The bot records winners with places `1..prizes_count`.

### 9.2. Bot-assisted random draw cap

This rule applies only to the optional random draw, not to manual paper draw.

Let:

- `n = prizes_count`;
- `m = last_ticket_number` at finish.

Then:

```text
max_prizes_per_ticket = ceil(n / m)
```

Minimum is 1.

Examples:

- 10 prizes / 3 tickets = 3.33 → max 4 prizes per ticket.
- 10 prizes / 100 tickets = 0.1 → max 1 prize per ticket.

Random draw rules:

- only after `finished`;
- select exactly `prizes_count` winners;
- one ticket may win multiple times up to `max_prizes_per_ticket`;
- a ticket at the cap is excluded from further selection;
- `m = 0` → no draw, show an error;
- use `secrets.SystemRandom`;
- store place, ticket, seed/log, timestamp in the audit;
- show results to admins/organizers only;
- manual paper draw remains the official method.

### 9.3. Printing

PDF tickets:

- A4;
- e.g., 10 tickets per page with cut lines;
- large ticket number and raffle title, optional logo;
- no names, organizations or personal data;
- numbers strictly in order, no gaps;
- count equals `last_ticket_number`;
- Unicode font (e.g., DejaVu Sans) for Cyrillic;
- generated only when raffle is `finished` or `archived`.

Optional CSV with one row per ticket for external printing.

### 9.4. Winner recording

Admin or organizer may record winners:

- places `1..prizes_count`;
- bot finds application and participant;
- show personal data only to admins/organizers;
- save result in the raffle archive (`finished`; after publication `archived`);
- may generate an announcement text but never publishes automatically.

Publication of personal data requires separate consent. Default publication is ticket number only.

---

## 10. Admin panel

### 10.1. Access

- `/admin` or "Admin panel".
- Roles: `admin`, `organizer`, `org_rep`.
- No access: "У вас нет доступа к админ-панели".
- Admin panel works in private chat with the bot. In groups, ask to use DM.
- Menu depends on role.

### 10.2. Menu

```text
/admin
├── Raffles
│   ├── Current raffle
│   ├── Create
│   ├── Edit
│   ├── Stop acceptance
│   ├── Resume acceptance
│   ├── Finish
│   └── Archive
├── Applications
│   ├── Pending review
│   ├── Confirmed
│   ├── Rejected
│   ├── All
│   └── Search by ID / user / payment code
├── Participants
│   ├── Total amount and tickets
│   └── Application history
├── Organizations and payment methods
│   ├── Organizations
│   └── Payment methods
├── Export
│   ├── CSV
│   ├── XLSX
│   ├── Print tickets (PDF)
│   └── Receipt archive (ZIP, phase 2)
├── Statistics
├── Roles
│   ├── Organizers (admin only)
│   └── Organization representatives
├── Settings
├── Audit
└── Integrity check
```

`org_rep` menu:

```text
/admin
├── My organization's applications
├── My organization's export
├── My organization's statistics
└── Help
```

### 10.3. Raffle management

- List with status filter.
- Card: title, price, minimum, prize count, dates, organizations, status, applications, confirmed amount, issued tickets.
- Create/edit wizard.
- Duplicate.
- Delete a draft without applications, or delete an archived raffle. Admin or organizer.
  Deleting an archive removes its applications, winners, participant totals, organization
  links and receipt objects. Users, organizations and the audit log remain. Confirm first.
- Start, stop, resume, finish, archive.
- Confirm dangerous actions.
- Finish/archive allowed to admin and organizer.

### 10.4. Applications

Filters:

- raffle;
- organization (admin/organizer all; org rep only own);
- payment method;
- status;
- search by public ID, payment code, Telegram ID, username.

List: 10 per page; ID, participant, organization, method, amount, status, date; open card.

Card:

- all data;
- receipt (resend file);
- payment instructions snapshot;
- status history;
- buttons Confirm, Reject, Request new receipt;
- confirmed: new ranges, participant total, reviewer;
- rejected: reason;
- resend user notification;
- "Remind reviewer" — manual reminder to organization chat; no automatic expiry;
- age in `pending_review`.

Org rep cannot open another organization's application even by direct ID. Check server-side.

### 10.5. Participants

Admin/organizer only:

- participant list;
- confirmed amount;
- ticket count;
- all numbers;
- number of applications and organizations;
- application history;
- search by Telegram ID, username, name, payment code.

Org reps do not see this section.

### 10.6. Organizations and payment methods

Admin/organizer only.

Organizations:

- list;
- add/edit: name, slug, description, logo, review chat, thread, contact, active;
- organizations with applications are disabled, not deleted;
- enable in raffle;
- test that the bot can write to chat;
- `/chatid` for admins/organizers shows chat and thread ID.

Payment methods:

- list;
- add: title, instructions;
- edit with preview;
- formatting buttons Bold, Code, Link;
- hint: copyable value in backticks; bold `*text*`;
- validation of MarkdownV2 and length;
- enable/disable;
- reorder;
- cannot disable the last active method while the organization is in an active raffle.

### 10.7. Export

Formats:

- CSV: UTF-8 with BOM, `;` separator.
- XLSX: sheets Applications, Participants, Tickets, Summary.
- PDF print tickets: only `finished` or `archived`.

Scope:

- default confirmed only; optionally all statuses;
- admin/organizer all organizations;
- org rep own organization only; no Participants sheet for org rep.

Applications columns:

```text
raffle, application_id, payment_code, organization, payment_method,
user_id, username, first_name, last_name, amount_byn,
tickets_awarded, tickets_total_after, first_ticket, last_ticket,
voided_first_ticket, voided_last_ticket, status,
submitted_at, confirmed_at, confirmed_by, rejection_reason
```

Participants columns:

```text
raffle, user_id, username, first_name, last_name,
confirmed_amount_byn, tickets_issued, ticket_ranges, applications_count, organizations
```

Tickets columns:

```text
ticket_number, user_id, username, source_application_id,
organization, payment_method, amount_byn
```

Summary:

```text
organization, applications_count, confirmed_count, total_amount_byn
raffle, prizes_count, participants_count, confirmed_amount_byn, tickets_issued
```

Rules:

- organization summary sums money only, not tickets;
- export only to the requester's DM;
- warn that the file contains personal data;
- filename: `raffle_<slug>_<YYYY-MM-DD>.xlsx`;
- split large files if Telegram limit is exceeded;
- the receipt ZIP archive (phase 2) is assembled from RustFS objects, not from local disk;
- audit who exported what and when.

### 10.8. Statistics

Per raffle:

- total applications;
- pending;
- confirmed;
- rejected;
- cancelled;
- confirmed amount;
- issued tickets;
- prize count;
- unique participants;
- average donation;
- conversion: start → receipt → confirmation;
- by organization: applications, confirmed, amount;
- by payment method: applications, amount.

Org rep sees own organization only.

### 10.9. Roles

- Organizer list — admin only.
- Org representative list — admin and organizer.
- Add organizer — admin only.
- Add representative — admin and organizer.
- Remove organizer — admin only.
- Remove representative — admin and organizer.
- Add by ID, forwarded message, or invite.
- List active/used invites; revoke invite.
- Audit changes.

### 10.10. Settings

Admin and organizer:

- channel URL/title;
- rules URL, privacy URL;
- rules version, privacy version;
- support contact;
- management log chat;
- max donation;
- receipt retention;
- two-step confirmation;
- optional random draw;
- timezone;
- greeting/rules text (optional).

### 10.11. Audit

Log:

- role grants/revocations;
- raffle create/edit/start/stop/finish;
- critical field changes;
- organization/payment method changes;
- confirm/reject/revision/void;
- export;
- settings changes;
- notification errors.

Filter by date, action, user; CSV export.

### 10.12. Integrity and monitoring

- counter vs ticket sum;
- participant totals;
- range gaps/overlaps;
- unnotified applications;
- pending count and age;
- card delivery status;
- organization chat health;
- participant totals mismatch;
- queue alerts;
- no auto-expiry of submitted applications.

---

## 11. Data model

Use these table names and fields unless a strong technical reason requires a change. Document any change.

### 11.1. `users`

| Field | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | Telegram ID |
| `username` | TEXT NULL | Without `@` |
| `first_name` | TEXT | |
| `last_name` | TEXT NULL | |
| `language_code` | TEXT NULL | |
| `created_at` | TIMESTAMPTZ | |
| `last_seen_at` | TIMESTAMPTZ | |
| `is_blocked` | BOOLEAN | |
| `rules_accepted_at` | TIMESTAMPTZ NULL | |
| `rules_version` | TEXT NULL | |
| `privacy_accepted_at` | TIMESTAMPTZ NULL | |
| `privacy_version` | TEXT NULL | |
| `age_confirmed_at` | TIMESTAMPTZ NULL | |
| `publish_name_consent_at` | TIMESTAMPTZ NULL | |

### 11.2. `roles`

| Field | Type | Notes |
|---|---|---|
| `id` | PK | |
| `user_id` | BIGINT FK → users | |
| `role` | ENUM | `admin`, `organizer`, `org_rep` |
| `organization_id` | FK → organizations NULL | Required for `org_rep` |
| `granted_by` | BIGINT NULL | NULL for env sync |
| `granted_at` | TIMESTAMPTZ | |
| `revoked_at` | TIMESTAMPTZ NULL | |
| `revoked_by` | BIGINT NULL | |

Unique partial index on active roles:
`(user_id, role, COALESCE(organization_id, 0)) WHERE revoked_at IS NULL`.

### 11.3. `organizations`

| Field | Type | Notes |
|---|---|---|
| `id` | PK | |
| `slug` | TEXT UNIQUE | |
| `name` | TEXT | |
| `description` | TEXT | |
| `logo_file_id` | TEXT NULL | |
| `review_chat_id` | BIGINT | |
| `review_thread_id` | BIGINT NULL | |
| `contact_url` | TEXT NULL | |
| `is_active` | BOOLEAN | |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

No payment fields on this table.

### 11.4. `payment_methods`

| Field | Type | Notes |
|---|---|---|
| `id` | PK | |
| `organization_id` | FK → organizations | |
| `title` | TEXT | Max 64 chars |
| `instructions_text` | TEXT | MarkdownV2, max 3000 chars |
| `is_active` | BOOLEAN | |
| `sort_order` | INTEGER | |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

### 11.5. `raffles`

| Field | Type | Notes |
|---|---|---|
| `id` | PK | |
| `slug` | TEXT UNIQUE | |
| `title` | TEXT | |
| `description` | TEXT | |
| `rules_text` | TEXT NULL | |
| `ticket_price` | NUMERIC(10,2) | |
| `min_amount` | NUMERIC(10,2) | Per payment |
| `prizes_count` | INTEGER | Default 1 |
| `currency` | CHAR(3) | `BYN` |
| `start_at` | TIMESTAMPTZ | |
| `end_at` | TIMESTAMPTZ | |
| `status` | ENUM | `draft`, `scheduled`, `active`, `closed`, `finished`, `archived` |
| `channel_url` | TEXT | |
| `channel_title` | TEXT | |
| `last_ticket_number` | INTEGER | Default 0 |
| `created_by` | BIGINT | |
| `created_at`, `updated_at` | TIMESTAMPTZ | |
| `finished_at` | TIMESTAMPTZ NULL | |

### 11.6. `raffle_organizations`

| Field | Type | Notes |
|---|---|---|
| `raffle_id` | FK → raffles | |
| `organization_id` | FK → organizations | |
| `is_enabled` | BOOLEAN | |
| `sort_order` | INTEGER | Admin lists only |

PK `(raffle_id, organization_id)`.

### 11.7. `applications`

| Field | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `public_id` | TEXT UNIQUE | e.g. `A-2026-000123` |
| `payment_code` | TEXT | 5 chars, unique per raffle |
| `raffle_id` | FK → raffles | |
| `user_id` | FK → users | |
| `organization_id` | FK → organizations | |
| `payment_method_id` | FK → payment_methods NULL | |
| `payment_method_title` | TEXT NULL | Snapshot |
| `payment_instructions_snapshot` | TEXT NULL | MarkdownV2 snapshot |
| `amount` | NUMERIC(10,2) NULL | NULL only in draft |
| `currency` | CHAR(3) | `BYN` |
| `tickets_awarded` | INTEGER | Delta, default 0 |
| `tickets_total_after` | INTEGER NULL | Participant total after confirmation |
| `status` | ENUM | See 7.6 |
| `receipt_type` | ENUM NULL | `photo`, `pdf`, `image` |
| `receipt_file_id` | TEXT NULL | |
| `receipt_file_unique_id` | TEXT NULL | |
| `receipt_object_key` | TEXT NULL | RustFS object key |
| `receipt_etag` | TEXT NULL | Object ETag/version for integrity checks |
| `receipt_mime` | TEXT NULL | |
| `receipt_size` | INTEGER NULL | |
| `submitted_at` | TIMESTAMPTZ NULL | |
| `reviewed_by` | BIGINT NULL | |
| `reviewed_at` | TIMESTAMPTZ NULL | |
| `rejection_reason` | TEXT NULL | |
| `first_ticket_number` | INTEGER NULL | Active range, confirmed only |
| `last_ticket_number` | INTEGER NULL | Active range, confirmed only |
| `voided_first_ticket_number` | INTEGER NULL | Historical voided range |
| `voided_last_ticket_number` | INTEGER NULL | Historical voided range |
| `card_chat_id` | BIGINT NULL | |
| `card_message_id` | BIGINT NULL | |
| `user_notified_at` | TIMESTAMPTZ NULL | |
| `notification_error` | TEXT NULL | |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

### 11.8. `participant_totals`

| Field | Type | Notes |
|---|---|---|
| `raffle_id` | FK → raffles | |
| `user_id` | FK → users | |
| `confirmed_amount` | NUMERIC(12,2) | |
| `tickets_issued` | INTEGER | |
| `updated_at` | TIMESTAMPTZ | |

PK `(raffle_id, user_id)`.

### 11.9. `audit_log`

| Field | Type | Notes |
|---|---|---|
| `id` | PK | |
| `entity_type` | TEXT | application, raffle, role, organization, payment_method, settings, export |
| `entity_id` | TEXT NULL | |
| `application_id` | FK NULL | |
| `raffle_id` | FK NULL | |
| `actor_id` | BIGINT NULL | |
| `action` | TEXT | |
| `old_status` | TEXT NULL | |
| `new_status` | TEXT NULL | |
| `payload` | JSONB NULL | |
| `created_at` | TIMESTAMPTZ | |

### 11.10. `invites`

| Field | Type | Notes |
|---|---|---|
| `id` | PK | |
| `token_hash` | TEXT UNIQUE | |
| `role` | ENUM | `organizer`, `org_rep` |
| `organization_id` | FK NULL | Required for `org_rep` |
| `created_by` | BIGINT | |
| `expires_at` | TIMESTAMPTZ | |
| `used_by` | BIGINT NULL | |
| `used_at` | TIMESTAMPTZ NULL | |
| `revoked_at` | TIMESTAMPTZ NULL | |
| `revoked_by` | BIGINT NULL | |

### 11.11. `settings`

| Field | Type | Notes |
|---|---|---|
| `key` | TEXT PK | |
| `value` | JSONB | |
| `updated_by` | BIGINT NULL | |
| `updated_at` | TIMESTAMPTZ | |

### 11.12. `winners`

| Field | Type | Notes |
|---|---|---|
| `id` | PK | |
| `raffle_id` | FK | |
| `place` | INTEGER | `1..prizes_count` |
| `ticket_number` | INTEGER | |
| `application_id` | FK | |
| `announced_at` | TIMESTAMPTZ NULL | |

Unique `(raffle_id, place)`. A ticket may occupy multiple places only in manual mode.

### 11.13. Constraints and indexes

- `ticket_price > 0`.
- `min_amount >= ticket_price`.
- `prizes_count >= 1` for `active`/`finished`.
- `end_at > start_at`.
- `raffles.last_ticket_number >= 0`.
- `amount` is not null from `awaiting_receipt` onward.
- Confirmed application: `tickets_awarded >= 1`, `first_ticket_number <= last_ticket_number`, `last - first + 1 = tickets_awarded`.
- Voided application: active range null; `voided_*` filled.
- `participant_totals.confirmed_amount >= 0`, `tickets_issued >= 0`.
- Unique `(raffle_id, first_ticket_number)` and `(raffle_id, last_ticket_number)` for non-null active ranges.
- Unique `(raffle_id, payment_code)`.
- Global unique `receipt_file_unique_id` (best effort).
- Partial unique active roles.
- Indexes: `applications(raffle_id, status)`, `applications(user_id)`, `applications(organization_id, status)`, `applications(raffle_id, first_ticket_number)`, `payment_methods(organization_id, is_active, sort_order)`.

---

## 12. Telegram formatting and messages

### 12.1. Formatting rules

- Parse mode: MarkdownV2. No HTML.
- Escape all dynamic values for MarkdownV2: `_`, `*`, `[`, `]`, `(`, `)`, `~`, `` ` ``, `>`, `#`, `+`, `-`, `=`, `|`, `{`, `}`, `.`, `!`.
- Escape literal special characters in static templates too. Examples below omit backslashes for readability.
- Supported formatting: `*bold*`, `_italic_`, `__underline__`, `~strikethrough~`, `` `code` ``, `[text](url)`.
- If Telegram rejects markup, send the same text without markup and notify the admin.
- Sending limit: 4096 characters; caption limit: 1024. Compress long number lists into ranges.
- Sums display with comma: `15,00 BYN`; exports use a dot.
- Dates display `21.10.2026 14:35`, Europe/Minsk; store UTC.
- Russian pluralization: 1 билет, 2 билета, 5 билетов; 1 номерок, 2 номерка, 5 номерков.
- Numbers: `4`, `1–3`, `1, 3–4`.
- Links: `[text](url)`. For users without username: `[Имя](tg://user?id={user_id})`.
- If support contact or channel URL is missing, omit the corresponding line and button.

### 12.2. Required Russian templates

Use these as the canonical copy. Adapt only for grammar and pluralization.

**Consent**

```text
Чтобы участвовать, примите правила розыгрыша и политику конфиденциальности.

[Правила розыгрыша]({rules_url})
[Политика конфиденциальности]({privacy_url})

Если требуется: подтвердите, что вам 18 лет или больше.
```

Buttons: `✅ Принимаю`; if age is required `✅ Мне 18+`; `❌ Отмена`.

**No active raffle**

```text
Сейчас нет активного розыгрыша.

Следующий анонс появится на канале «{channel_title}».
```

**Active raffle greeting**

```text
🎗 *{raffle_title}*

{raffle_description}

💰 Цена билета: *{ticket_price} BYN*
🏆 Призов: *{prizes_count}*
📅 Приём заявок: до {end_at}
🎟 За каждые {ticket_price} BYN — 1 билет.
Билеты считаются по общей сумме всех ваших подтверждённых пожертвований в этом розыгрыше.

Выберите организацию, которой хотите помочь:
```

**Payment method selection**

```text
🏢 *{organization_name}*

Выберите способ оплаты:
```

**Payment instructions**

```text
🏢 *{organization_name}*
💳 *{payment_method_title}*

{payment_instructions}

🔖 Код платежа: `{payment_code}`
Если банк позволяет указать назначение платежа, добавьте этот код — организации будет проще найти ваш перевод.

После перевода введите сумму в BYN, которую вы отправили на счёт организации.
Минимальная сумма для участия — *{min_amount} BYN*.
За каждые *{ticket_price} BYN* — 1 билет.

Перевод отправляется напрямую организации. Бот не принимает и не хранит денежные средства.
```

**Invalid amount**

```text
❌ Минимальная сумма для участия — {min_amount} BYN. Пожалуйста, введите корректную сумму.
```

**Amount preview and receipt prompt**

```text
{amount} BYN. С учётом уже подтверждённых {current_amount} BYN всего будет {preview_amount} BYN = {preview_tickets} билетов.
После проверки чека вы получите ещё {preview_delta} номерков.

📎 Пришлите чек одним файлом:
• фотографией (как фото в Telegram);
• PDF-документом;
• изображением JPEG/PNG, отправленным документом.

На чеке должны быть видны получатель, сумма, дата и статус операции.
```

**Invalid file type**

```text
❌ Принимаются фото, PDF или изображение (JPEG/PNG) документом. Текст, стикеры и другие документы не подходят. Пришлите, пожалуйста, чек подходящим файлом.
```

**Pending review**

```text
✅ Заявка №{public_id} отправлена на проверку.

Организация: {organization_name}
Способ оплаты: {payment_method_title}
Сумма: {amount} BYN

Билеты рассчитываются по общей сумме всех подтверждённых пожертвований в этом розыгрыше. Номера присваиваются после подтверждения оплаты.
```

**Admin card**

```text
📥 *Новая заявка на розыгрыш*

🆔 Заявка: `{public_id}`
🔖 Код платежа: `{payment_code}`
👤 Участник: {user_mention}
🏢 Организация: *{organization_name}*
💳 Способ оплаты: {payment_method_title}
💰 Сумма: *{amount} BYN*
📊 Уже подтверждено у участника: {current_amount} BYN
🎫 Предварительно: +{preview_delta} билета (всего {preview_tickets})
📅 Дата: {submitted_at}
🎗 Розыгрыш: {raffle_title}
```

**User confirmation**

```text
✅ *Оплата подтверждена!*

Заявка №{public_id}.
Вы пожертвовали *{amount} BYN* в *{organization_name}*.

Всего подтверждено: *{total_amount} BYN*.
Новые номерки: *{new_numbers}*.
Всего у вас *{total_tickets}* билетов: *{all_numbers}*.

Желаем удачи!
⚠️ Пожалуйста, не забудьте подписаться на канал [«{channel_title}»]({channel_url}) — именно там мы объявим результаты розыгрыша!
```

Add an inline button "📣 Подписаться на канал". Subscription is not checked and does not block participation.

**Rejection**

```text
❌ К сожалению, не удалось подтвердить оплату по заявке №{public_id}.

Причина: {reason}

Если вы считаете, что это ошибка, напишите {support_contact}.
```

**New receipt request**

```text
📎 Чек не удалось прочитать. Пришлите, пожалуйста, более чёткое фото, PDF или изображение документом по заявке №{public_id}.
```

**Acceptance closed**

```text
⛔ Приём заявок завершён! Розыгрыш закрыт, ожидайте результатов на канале «{channel_title}».
```

Add a "Go to channel" button if the URL is set.

---

## 13. Security and privacy

### 13.1. Authorization

- Check permissions on every action, not only in menus.
- Never trust `callback_data`; re-check role, organization and application status in the database.
- A card confirmation is accepted only from the chat where the card was sent (`card_chat_id`), or from the authorized reviewer's DM via the admin panel. A forwarded card must not allow confirmation in a foreign chat.
- Self-confirmation is forbidden for all roles.
- If the only reviewer is the participant, route to the management chat; if no second reviewer exists, block and explain.

### 13.2. Rate limits

- `/start`: 1 per second.
- Amount submissions: 10 per minute.
- Receipt uploads: 5 per 10 minutes.
- Applications: 20 per user per raffle.

### 13.3. Files

- Max `RECEIPT_MAX_MB`, default 20 MB.
- Validate MIME, extension and magic bytes.
- Reject executables and unknown types.
- Store receipts in a private RustFS bucket; generate object keys in the bot, never from user input.
- Use `file_id` for resend; fall back to the RustFS object if `file_id` fails.
- Delete RustFS objects after `RECEIPT_RETENTION_DAYS` after raffle finish; keep metadata and audit. An optional bucket lifecycle policy is a second line of defense.
- No public bucket or public receipt URLs. Keep the RustFS console off the public internet.
- In production use TLS and unique RustFS credentials; never use default credentials.
- Temporary local files are allowed only during upload and must be deleted immediately.
- If RustFS is unavailable, do not move the application to `pending_review`; retry and alert the admin.

### 13.4. MarkdownV2

- Escape all dynamic data.
- Validate admin payment method markup with preview.
- Fall back to plain text if Telegram rejects markup.
- Do not use HTML.

### 13.5. Privacy

- Minimal data: Telegram ID, username, name, amount, organization, method, receipt, timestamps.
- Consent: rules, privacy, age if required, optional name publication.
- Access: org reps only their organization; admins/organizers all.
- Receipts contain personal data; do not post exports in group chats.
- Support data deletion requests subject to accounting retention.
- Logs must not contain tokens, full bank details, or receipt content.
- PostgreSQL and RustFS bucket backups must be protected and tested.

### 13.6. Legal

This is not legal advice. Before launch, a Belarusian lawyer should review:

- raffle rules;
- charity and tax requirements;
- personal data processing;
- age restrictions;
- prize fulfillment and taxation.

Rules must include: organizer, dates, ticket price, minimum, prize count, draw method, winner notification, forfeiture, taxes, privacy, support.

---

## 14. Technology and architecture

### 14.1. Stack

- Python 3.11+
- **uv** for dependency management, virtualenv and command execution
- aiogram 3.x
- SQLAlchemy 2.x async
- asyncpg
- Alembic
- PostgreSQL 15+ in all environments, including development and tests
- RustFS (S3-compatible object storage) for receipts
- aioboto3 (async S3 client)
- APScheduler
- openpyxl
- reportlab
- pypdf
- Pydantic Settings
- Docker Compose
- pytest, pytest-asyncio
- ruff

### 14.2. Repository layout

```text
.
├── pyproject.toml
├── uv.lock
├── .python-version
├── .env.example
├── compose.yaml
├── alembic.ini
├── README.md
└── bot/
    ├── main.py
    ├── config.py
    ├── handlers/
    │   ├── participant.py
    │   ├── my.py
    │   ├── review.py
    │   └── admin/
    │       ├── raffles.py
    │       ├── applications.py
    │       ├── participants.py
    │       ├── organizations.py
    │       ├── payment_methods.py
    │       ├── roles.py
    │       ├── exports.py
    │       └── settings.py
    ├── services/
    │   ├── raffles.py
    │   ├── applications.py
    │   ├── tickets.py
    │   ├── participant_totals.py
    │   ├── receipts.py
    │   ├── storage.py
    │   ├── notifications.py
    │   ├── exports.py
    │   ├── roles.py
    │   └── integrity.py
    ├── db/
    │   ├── models.py
    │   ├── repositories/
    │   └── migrations/
    ├── keyboards/
    ├── middlewares/
    │   ├── db.py
    │   ├── auth.py
    │   └── throttle.py
    ├── filters/
    │   └── roles.py
    └── texts/
        └── ru.py
```

Principles:

- Thin handlers, business logic in services.
- DB access through repositories.
- Role checks in middleware, filters and services.
- Notification service with retries.
- Scheduler with single-instance protection.
- All UI strings in `texts/ru.py`.

### 14.3. Configuration

| Variable | Required | Default | Description |
|---|---:|---|---|
| `BOT_TOKEN` | yes | — | BotFather token |
| `ADMIN_IDS` | yes | — | Comma-separated Telegram IDs |
| `DATABASE_URL` | yes | — | PostgreSQL DSN, e.g. `postgresql+asyncpg://raffle:raffle@localhost:5432/raffle` |
| `RUSTFS_ENDPOINT` | yes | — | S3 endpoint, e.g. `http://rustfs:9000` |
| `RUSTFS_ACCESS_KEY` | yes | — | RustFS access key |
| `RUSTFS_SECRET_KEY` | yes | — | RustFS secret key |
| `RUSTFS_BUCKET` | no | `raffle-receipts` | Private receipt bucket |
| `RUSTFS_REGION` | no | `us-east-1` | S3 region |
| `RUSTFS_USE_SSL` | no | `true` | TLS for RustFS; local compose may use `false` |
| `RUSTFS_FORCE_PATH_STYLE` | no | `true` | Path-style addressing for S3-compatible storage |
| `TZ` | no | `Europe/Minsk` | Timezone |
| `DEFAULT_TICKET_PRICE` | no | `5.00` | New raffle default |
| `DEFAULT_MIN_AMOUNT` | no | `5.00` | New raffle default |
| `MAX_DONATION` | no | `10000.00` | Max single application |
| `RECEIPT_MAX_MB` | no | `20` | Max receipt size |
| `RECEIPT_RETENTION_DAYS` | no | `90` | RustFS receipt object retention |
| `CHANNEL_URL` | no | — | Default channel |
| `CHANNEL_TITLE` | no | `Тетрадь с полями🪶` | Channel title |
| `RULES_URL` | yes | — | Raffle rules URL |
| `PRIVACY_URL` | yes | — | Privacy policy URL |
| `SUPPORT_CONTACT` | no | — | Support contact |
| `MANAGEMENT_LOG_CHAT_ID` | no | — | Management chat for copies/fallback |
| `ENABLE_RANDOM_DRAW` | no | `false` | Optional random draw |
| `CONFIRM_TWO_STEP` | no | `true` | Two-step confirmation |
| `WEBHOOK_URL` | no | — | Webhook URL; polling if empty |
| `WEBHOOK_SECRET` | no | — | Webhook secret |
| `LOG_LEVEL` | no | `INFO` | Log level |

Priority: raffle settings → `settings` table → environment defaults.

Startup validation:

- `BOT_TOKEN`, `ADMIN_IDS`, `DATABASE_URL`, `RUSTFS_ENDPOINT`, `RUSTFS_ACCESS_KEY`, `RUSTFS_SECRET_KEY`, `RULES_URL`, `PRIVACY_URL` present;
- `DATABASE_URL` starts with `postgresql+asyncpg://`; reject SQLite and other drivers;
- `DEFAULT_MIN_AMOUNT >= DEFAULT_TICKET_PRICE`;
- `MAX_DONATION > 0`;
- `RECEIPT_MAX_MB <= 20`;
- RustFS is reachable and the private bucket exists or can be created;
- fail fast with a clear log message.

### 14.4. Deployment

- Docker Compose: `bot`, `postgres` (PostgreSQL 15+) and `rustfs` (RustFS S3-compatible storage). No SQLite, no Redis.
- Local development runs `docker compose up -d postgres rustfs`.
- RustFS:
  - official image `rustfs/rustfs` (pin a tested version in production);
  - S3 API on port 9000, console on 9001; do not expose the console publicly;
  - service env: `RUSTFS_ACCESS_KEY`, `RUSTFS_SECRET_KEY`, `RUSTFS_ADDRESS=:9000`, `RUSTFS_CONSOLE_ADDRESS=:9001`, `RUSTFS_CONSOLE_ENABLE=true`; command `/data`;
  - private bucket `raffle-receipts`, created on startup if missing;
  - credentials must allow bucket create/list and object put/get/delete, or the bucket must be pre-created;
  - persistent volume mounted at `/data`;
  - TLS and unique credentials in production; never use default credentials.
- Dependency management and command execution use `uv`:
  - `uv sync --all-groups`
  - `uv run alembic upgrade head`
  - `uv run python -m bot.main`
  - `uv run pytest`
  - `uv run ruff check .`
- Volumes for PostgreSQL data and RustFS data. There is no permanent local receipt directory.
- Compose healthchecks for PostgreSQL and RustFS; the bot waits for both before starting.
- FSM uses `MemoryStorage`; drafts and critical state are persisted in PostgreSQL and reconstructed after restart.
- `.env.example` contains PostgreSQL `DATABASE_URL` and RustFS `RUSTFS_*` examples.
- Alembic migrations on startup or as a separate command.
- Long polling for MVP; webhook optional.
- One scheduler instance; distributed lock if scaled.
- NTP, backups, health check.
- Graceful shutdown.

---

## 15. Testing

### 15.1. Unit tests

- amount parsing and validation;
- floor calculation and remainder;
- cumulative ticket preview;
- range assignment;
- participant totals delta;
- void recalculation;
- prize cap `ceil(n/m)`;
- role checks;
- receipt type validation;
- MarkdownV2 escaping;
- consent version logic;
- export column/range formatting.

### 15.2. Integration tests

All integration and concurrency tests run against a real PostgreSQL 15+ instance, for example the `postgres` Docker Compose service or Testcontainers. Run them with `uv run pytest`.

- full participant flow;
- photo, PDF, image document;
- confirm, reject, revision;
- double-click;
- concurrent confirmation by two reviewers;
- concurrent confirmation of two payments of the same user in different organizations;
- 9 BYN + 6 BYN = 3 tickets;
- 9 BYN + 5 BYN = 2 tickets;
- remainders of different users are not pooled;
- non-contiguous ranges;
- deadline and stop;
- draft expiry;
- role invites and revocation;
- self-confirm fallback;
- finish/print/void locks;
- RustFS upload, download and deletion;
- RustFS upload failure does not move an application to `pending_review`;
- fallback to the RustFS object when Telegram `file_id` is unavailable;
- export and PDF;
- consent and rules version change.

### 15.3. Security tests

- org rep confirms another organization;
- participant calls admin callback;
- oversized file;
- fake PDF/image;
- duplicate receipt;
- self-confirmation;
- forged callback/chat.

### 15.4. Load test

- 100 concurrent users;
- 10,000 applications per raffle;
- export 10,000 rows;
- PDF 10,000 tickets.

---

## 16. Acceptance criteria

### 16.1. Raffles

- [ ] AC-01. Create a raffle with ticket price, dates, organizations and `prizes_count`.
- [ ] AC-02. Draft/scheduled raffle is not visible to participants.
- [ ] AC-03. Only one raffle is current (`active`/`closed`).
- [ ] AC-04. Start at `start_at`, close at `end_at`.
- [ ] AC-05. Offline overdue raffles close on startup.
- [ ] AC-06. Closed raffle accepts no new applications but confirms submitted ones.
- [ ] AC-06a. Draft without receipt cannot submit after stop.
- [ ] AC-07. New raffle numbering starts at 1.
- [ ] AC-08. Duplicate raffle resets counter and dates.

### 16.2. Participant

- [ ] AC-09. All active organizations are shown in per-user randomized order.
- [ ] AC-10. Order is stable for one user within a raffle.
- [ ] AC-11. Organization selection shows payment methods; one method goes straight to instructions.
- [ ] AC-11a. Instructions contain copyable values in backticks; payment code is exactly 5 characters.
- [ ] AC-12. Invalid amount, below minimum, zero, negative, too many decimals and over max are rejected.
- [ ] AC-13. Preview uses the participant's confirmed total.
- [ ] AC-14. Photo, PDF and JPEG/PNG document are accepted; text/sticker/other documents are rejected.
- [ ] AC-15. PDF and images are validated by MIME and magic bytes.
- [ ] AC-16. Duplicate `file_unique_id` is rejected (best effort).
- [ ] AC-17. Receipt upload sets `pending_review`, not numbers.
- [ ] AC-18. `/my` shows status, total amount, total tickets and all ranges.
- [ ] AC-18a. Payment code appears in instructions, admin card and export.
- [ ] AC-18b. 9 BYN to one organization + 6 BYN to another = 3 tickets total.
- [ ] AC-18c. 9 BYN for one user + 9 BYN for another = 2 tickets, not 3.
- [ ] AC-18d. Non-contiguous numbers display as `1, 3–4`.
- [ ] AC-18e. After 9 BYN, 5 BYN gives 2 total tickets; 6 BYN gives 3 total tickets; minimum 5 per payment holds.

### 16.3. Review and tickets

- [ ] AC-19. Card goes to the organization chat with receipt, method, amount, preview and buttons.
- [ ] AC-20. Org rep confirms only own organization; self-confirmation is forbidden.
- [ ] AC-20a. If the only org rep is the participant, card goes to management chat and admin/organizer confirms.
- [ ] AC-21. Confirmation recalculates participant totals and issues only the delta.
- [ ] AC-22. Double-click and two-reviewer race do not duplicate.
- [ ] AC-23. Two simultaneous payments of the same user in different organizations do not duplicate or lose tickets.
- [ ] AC-24. Rejection consumes no numbers and changes no totals.
- [ ] AC-25. After mixed confirmations/rejections `last_ticket_number` equals total issued tickets, no gaps.
- [ ] AC-26. User gets new ranges and all current ranges.
- [ ] AC-27. Notification failure retries; tickets are not rolled back.
- [ ] AC-28. Void only last range, admin/organizer, active/closed; totals recalculated.
- [ ] AC-28a. Revision issues no tickets and preserves original `submitted_at`.
- [ ] AC-28b. Voided range moves to `voided_*`; freed numbers can be reused.
- [ ] AC-28c. PDF print is unavailable before `finished`; void is forbidden after `finished`.
- [ ] AC-29. Integrity check detects counter/total mismatch.

### 16.4. Roles and admin

- [ ] AC-30. Participant has no `/admin`.
- [ ] AC-31. Org rep sees and confirms only own organization.
- [ ] AC-32. Organizer can do everything admin can, including finish/archive/settings/organizations/export/void.
- [ ] AC-33. Organizer cannot assign/remove organizers; admin can.
- [ ] AC-34. Organizer and admin can assign org reps.
- [ ] AC-35. Invite is one-time and expires.
- [ ] AC-36. Revoked role stops working immediately.
- [ ] AC-37. Role/application actions are audited.
- [ ] AC-38. Only roles `participant`, `org_rep`, `organizer`, `admin` exist.

### 16.5. Payment methods

- [ ] AC-39. One organization can have multiple methods; participant selects one.
- [ ] AC-40. Method is free-form MarkdownV2 with backticks; no structured bank fields.
- [ ] AC-41. Method snapshot is stored in the application and does not change on edit.
- [ ] AC-42. Raffle cannot activate if an organization has no active method.
- [ ] AC-43. Admin/organizer manage methods; org rep cannot.

### 16.6. Export and draw

- [ ] AC-44. CSV/XLSX contains correct applications, participants, amounts, methods and ranges.
- [ ] AC-45. Org rep exports only own organization; no Participants sheet.
- [ ] AC-46. PDF contains exactly `last_ticket_number` tickets, no names or organizations.
- [ ] AC-47. Finish blocked by pending unless explicitly expired.
- [ ] AC-48. Organization summary sums money only; participant totals are global.

### 16.7. Security, consent, monitoring

- [ ] AC-49. All dynamic data is MarkdownV2-escaped; user names cannot break messages.
- [ ] AC-50. Oversized and fake files are rejected.
- [ ] AC-51. Secrets are not committed or logged.
- [ ] AC-52. Restart preserves confirmed numbers and drafts.
- [ ] AC-53. Daily PostgreSQL and RustFS bucket backup is configured and tested.
- [ ] AC-54. Invalid MarkdownV2 in payment method does not break sending; fallback plain text.
- [ ] AC-55. Concurrent confirmations do not diverge `participant_totals`.
- [ ] AC-56. Admin sees pending count/age; "Remind" sends a message to the organization chat.
- [ ] AC-57. Bot checks organization chat availability and alerts on failure.
- [ ] AC-58. Submitted applications never auto-expire; only authorized user sets `expired`; drafts may expire.
- [ ] AC-59. Without rules/privacy consent, organization buttons are hidden; consent/version stored.
- [ ] AC-60. Rules version change requires re-consent.
- [ ] AC-61. Winner name is published only with separate consent; default is ticket number.

### 16.8. Prizes

- [ ] AC-62. `prizes_count` is stored; activation requires >= 1.
- [ ] AC-63. 10 prizes / 3 tickets → cap 4; 10 / 100 → cap 1.
- [ ] AC-64. Manual draw ignores the cap; organizer may record any existing ticket within `1..prizes_count`.
- [ ] AC-65. Random draw selects exactly `prizes_count` winners and enforces `ceil(prizes_count / last_ticket_number)`.
- [ ] AC-66. Receipts are uploaded to a private RustFS bucket; PostgreSQL stores only metadata and `receipt_object_key`, with no binary blobs or permanent local files.
- [ ] AC-67. A RustFS upload failure does not move the application to `pending_review`; the user is asked to retry and the admin is alerted after repeated failures.
- [ ] AC-68. If Telegram `file_id` is unavailable, the bot sends the receipt from RustFS; if both are unavailable, it shows an error and does not lose the application.

---

## 17. Implementation plan

### Phase 1 — Scaffold

- `pyproject.toml`, `uv.lock`, `.python-version`.
- `uv sync --all-groups`.
- Config with Pydantic Settings.
- Async DB session using `asyncpg`.
- RustFS S3 client (`aioboto3`) and private bucket bootstrap.
- FSM `MemoryStorage` with DB-backed draft recovery.
- Alembic.
- Docker Compose with PostgreSQL 15+ and RustFS for local development.
- Logging.
- Health check.
- `.env.example` with PostgreSQL DSN.
- README skeleton with `uv` commands.

**Exit:** `uv run python -m bot.main` starts, `/start` responds, `uv run alembic upgrade head` applies against PostgreSQL.

### Phase 2 — Users, roles, invites

- Users table.
- Roles table and sync `ADMIN_IDS`.
- Invite tokens.
- Role middleware/filters.
- Role audit.
- `/admin` shell with role-based menu.

**Exit:** admin/organizer/org_rep access works; no `org_viewer`.

### Phase 3 — Organizations and payment methods

- Organization CRUD.
- Payment method CRUD with MarkdownV2 preview.
- `/chatid`.
- Chat test message.
- Activation checks.

**Exit:** organization with multiple methods can be configured.

### Phase 4 — Raffles

- Raffle CRUD and lifecycle.
- One-current rule.
- Scheduler start/stop.
- Prize count.
- Admin raffle card.

**Exit:** raffle can be created, activated, stopped, finished, archived.

### Phase 5 — Participant flow

- Consent.
- Organization selection with randomized order.
- Method selection.
- Amount validation and preview.
- Receipt upload for photo/PDF/image.
- Download from Telegram, upload to RustFS, store metadata and object key.
- Draft persistence.
- `/my`.

**Exit:** participant can submit a receipt, the object is in RustFS, and the application is `pending_review`.

### Phase 6 — Review and tickets

- Card routing.
- Confirm/reject/revision.
- Two-step confirmation.
- Participant totals and atomic ticket issue.
- Void.
- Number formatting.
- Notification retries.
- Integrity check.

**Exit:** 9+6=3, concurrent tests pass, no gaps.

### Phase 7 — Prizes, exports, PDF

- `prizes_count` and cap.
- Optional random draw.
- Winner recording.
- CSV/XLSX exports.
- Print PDF with DejaVu Sans.
- Statistics.

**Exit:** exports and PDF pass.

### Phase 8 — Hardening

- Rate limits.
- File validation.
- MarkdownV2 escaping tests.
- Privacy/consent.
- Monitoring/reminders.
- Load tests.
- README.
- Final acceptance run.

---

## 18. Assumptions and open questions

### 18.1. Assumptions

- One current raffle at a time.
- BYN only.
- Manual payment verification.
- Organizations provide payment method text and review chats.
- Payment methods are per organization, not per raffle.
- Minimum amount is per payment, not cumulative.
- The bot is not a payment agent.
- Manual paper draw is the official draw method.
- Random draw is optional and off by default.
- `finished` means all tickets are confirmed and locked.
- Printing is allowed only after `finished`.
- Void is forbidden after `finished`.
- Submitted applications are not auto-expired.
- Receipts are stored in RustFS; PostgreSQL holds metadata only; no permanent local receipt directory is used.
- RustFS is reachable from the bot; the bucket is private; production uses TLS and unique credentials.

### 18.2. Open questions for the product owner

1. Organization list, descriptions, review chats and payment method texts.
2. Review chat structure: separate chat per organization or one forum with topics.
3. Channel URL and whether subscription should ever be checked.
4. Print ticket layout: tickets per A4, logos, QR codes, signatures.
5. Expected load and deployment target; RustFS single-node vs cluster, TLS and bucket backup/retention.
6. Privacy policy, rules URL, age restriction and legal review.
7. Support contact.
8. Whether to add receipt ZIP archive in MVP.
9. Whether to record winners in the bot and generate announcements.
10. Whether admins/organizers may correct an application amount from the receipt (not in this spec).

When an open question blocks implementation, use the safest default above and document it in `ASSUMPTIONS.md`.

---

## 19. Appendix A — End-to-end example

1. Admin creates "Autumn 2026": ticket price 5 BYN, 3 prizes, acceptance 01.11.2026 10:00 to 30.11.2026 23:59, organizations A and B, channel "Тетрадь с полями🪶".
2. Organization A has methods "Card BYN" and "ERIP"; organization B has "IBAN".
3. A participant opens the bot, accepts rules/privacy, and sees organization buttons in random order: B, A.
4. Selects A, then "Card BYN". Bot shows card number in backticks, payment code `K7M2Q`, and asks for the amount.
5. Enters 9. Bot: "9,00 BYN = 1 билет. После проверки чека вы получите 1 номер."
6. Uploads a PDF.
7. Application `A-2026-000123` goes to organization A's chat with amount, method and buttons.
8. Org A reviewer finds the payment by code/amount/date, verifies the receipt, presses Confirm, then "Yes, issue numbers".
9. Bot issues number 1, sends the user a message, edits the card.
10. Another participant donates 5 BYN to organization B and gets number 2.
11. The first participant opens the bot again, selects B, "IBAN", enters 6.
12. Bot: "С учётом уже подтверждённых 9,00 BYN всего будет 15,00 BYN = 3 билета. После проверки чека вы получите ещё 2 номерка."
13. Org B reviewer confirms. Bot issues 3–4 and says: "Всего у вас 3 билета: 1, 3–4."
14. A third participant donates 9 BYN to B and gets 1 ticket (number 5); their 4 BYN remainder is not pooled with the first participant's remainder.
15. At 30.11.2026 23:59 the raffle closes automatically.
16. Organizer processes remaining applications and checks integrity: `last_ticket_number` equals total issued tickets; participant totals match.
17. Organizer finishes the raffle, exports the PDF, prints and runs the paper draw.
18. The first prize goes to ticket 3. Organizer records it, sees the participant, publishes the result.

---

## 20. Appendix B — Agent checklist

Before submitting work, confirm:

- [ ] No `org_viewer` role.
- [ ] No hardcoded organizations.
- [ ] No structured payment fields on organizations.
- [ ] Payment methods are MarkdownV2 with backticks and snapshots.
- [ ] No HTML anywhere.
- [ ] MarkdownV2 escaping tests pass.
- [ ] Ticket delta calculation and participant totals tests pass.
- [ ] 9+6=3 and 9+5=2 tests pass.
- [ ] Remainders are per participant.
- [ ] Non-contiguous ranges are formatted correctly.
- [ ] Confirmation transaction locks application, participant totals, raffle in that order.
- [ ] Void moves ranges to `voided_*` and reuses numbers.
- [ ] Print only after `finished`; void only before `finished`.
- [ ] Self-confirmation is blocked; fallback to management chat works.
- [ ] Submitted applications do not auto-expire.
- [ ] `prizes_count` and `ceil(n/m)` cap work; manual draw ignores cap.
- [ ] Image document receipts are accepted.
- [ ] Receipts are stored in RustFS; PostgreSQL has metadata only; no permanent local receipt directory.
- [ ] RustFS upload failure is handled without losing the application.
- [ ] Telegram `file_id` fallback to RustFS works.
- [ ] Payment code is exactly 5 characters.
- [ ] `MANAGEMENT_LOG_CHAT_ID` used for copies/fallback.
- [ ] Rules/privacy/age/name consent is stored with versions.
- [ ] Admin/organizer permissions match the matrix.
- [ ] Org rep scope is enforced server-side.
- [ ] Exports and PDF are correct.
- [ ] Audit log covers financial and role actions.
- [ ] No secrets or receipt files committed.
- [ ] `uv sync`, `uv run pytest` against PostgreSQL, `uv run ruff check`, migrations, Docker and README pass.
