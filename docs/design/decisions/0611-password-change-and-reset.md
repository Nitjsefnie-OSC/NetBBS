# Issue #611 — password change and reset — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

Until this issue shipped, `users.password_hash` was written once, at account
creation, and had no update path anywhere: no Profile field, no user-detail action, no admin
CLI command. A forgotten password meant delete-and-recreate, which loses the
account's history and, per #594, frees its Link identity for the next
registrant. Normative description: §4.1.

**Decision 1 — one domain function, three surfaces.** `set_password` is the
only writer, in the same transactional shape as the other account setters;
the Profile field, the user-detail `[P]assword` action and the CLI subcommand
all go through it, and the two screens share one implementation
(`netbbs.net.password_screen`), the same shape the SSH-key screen already
has. Who may call it is the caller's decision; what must hold regardless
(no blank password, no clearing without a key) is the function's.

**Decision 2 — the proof depends on who acts, not on the target's level.**
An account acting on itself proves the current password first; a SysOp
acting on another account does not, since they cannot know it, and the audit
row names them. A SysOp may reset another SysOp's password, matching what the
key screen already allows a SysOp to do to any account; the usable-SysOp
invariant (§4.3) is unaffected because a reset never removes a way in. The
local CLI never asks for a current password: filesystem access to the
database is its trust boundary, as for the rest of that tool, and its
purpose is the locked-out SysOp.

**Decision 3 — the in-session proof is throttled by the login throttle.**
`Session.login_throttle` is set once at login and the current-password
prompt charges it before verifying, in the same order as the login prompt.
Rejected: a separate per-session counter, which would have been a second
budget with its own limits to explain.

**Decision 4 — a key-only account sets its first password without proof.**
The session that reached the screen authenticated by key. Rejected: refusing
until a SysOp intervenes, which would make the only self-service route out of
key-only depend on someone else.

**Decision 5 — masked entry is a documented exception to §3.5, not a draft
editor.** Current password, new password, confirmation: three masked
prompts, each cancelling on a blank line, nothing written before the last.
Rejected: a draft editor, which would have to hold the plaintext password in
the draft across redraws so that `[S]ave` had something to save, and whose
"inspect before saving" value is nil for a value the caller cannot see. §3.5
and `AGENTS.md` both list the exception.

**Decision 6 — a caller's own choice meets the registration floor.**
Self-service applies `MIN_REGISTRATION_PASSWORD_LENGTH`, as both
registration prompts do for a password a remote caller picks; otherwise
Profile would be a way around the floor one screen after registration. A
SysOp setting someone's password keeps the latitude the create-user screen
already gives them.

**Decision 7 — Argon2 stays off the database lane.** The foreground
`DatabaseLane` has one worker; a hash or verification there stalls every
other interactive database operation for its duration and bypasses the
bounded password worker login uses. Both screens and the CLI therefore hash
and verify through that worker (`hash_password_off_loop`,
`verify_password_off_loop`) and run only the short transaction
(`set_password_hash`, `load_password_hash`) on the lane. The synchronous
`set_password`/`password_matches` remain for tests and for callers that own
their thread.

**Declined — guarding this setter against SQLite rowid reuse.** A target
deleted and another account created while the screen waits for input could
inherit the id and receive the update. Every setter in `netbbs.auth.users`
re-fetches by id the same way, and the case needs the highest-id account
deleted and re-created under an open SysOp screen, which is past the
single-operator boundary this project calibrates against. The identity-reuse
question is issue #594's, and its answer applies to all the setters at once.

**Not done, deliberately.** A password change does not end the account's
other live sessions; a caller who suspects a compromise asks the SysOp to
disable the account, which does. No self-service recovery exists: there is no
email or other out-of-band channel to send anything through, so "forgot my
password" is a SysOp action, and the Profile help text says so.
