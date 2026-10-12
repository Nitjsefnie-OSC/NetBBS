# NetBBS documentation

Choose the handbook for what you want to do. The handbooks describe current
NetBBS; release notes describe the version named in their title.

| Audience | Handbook |
| --- | --- |
| Callers | [User handbook](NetBBS-User-Handbook.md): connect, navigate, post, chat, exchange files, and play |
| SysOps | [SysOp handbook](NetBBS-SysOp-Handbook.md): install, configure, run, moderate, back up, and recover |
| Developers | [Developer handbook](NetBBS-Developer-Handbook.md): build doors, implement NetBBS Link, and contribute |

## SysOp references

- [Door setup and compatibility](NetBBS-door-guide.md): game-specific setup,
  persistent data, companion services, and the tested compatibility matrix.
- [Disaster recovery drill](NetBBS-disaster-recovery-drill.md): rehearse recovery
  on disposable state before you need it on a real node.
- [Service examples](../examples/README.md): NetBSD rc.d and Linux systemd.

## Developer and validation references

- [Architecture and product design](NetBBS-design-doc.md): normative product,
  protocol, authority, presentation, and compatibility decisions.
- [Engineering record](NetBBS-worklog.md): durable implementation constraints
  and testing lessons.
- [NetBBS Link dogfood plan](NetBBS-link-dogfood-plan.md) and
  [public-readiness gate](NetBBS-phase4-readiness.md): operational validation
  still needed before broader federation claims.
- [Website maintenance](../web/README.md): source pages, capture rules,
  validation, and deployment.

The design and engineering references support the developer handbook.
Callers and SysOps do not need to read them first.

## Releases and outstanding work

[GitHub releases](https://github.com/Thiesi/NetBBS/releases) provide downloads
and release history. The `NetBBS-v*-release-notes.md` files in this directory
are **historical records**, not alternative installation guides or current
feature lists. Read the notes for your upgrade, then follow the current
SysOp handbook.

[GitHub issues](https://github.com/Thiesi/NetBBS/issues) track bugs, pending
features, and manual validation. An open implementation tracker can include
shipped functionality whose hands-on acceptance checks remain unfinished.

## Keeping these documents useful

Put caller instructions in the user handbook, operating procedures in the
SysOp handbook, and contracts or implementation details in the developer
handbook and its references. Keep one detailed explanation per topic and
link to it. Update present-tense status in place; keep release history in
release notes and Git history. Verify commands and menu names against the
current implementation, and distinguish automated evidence from manual
or cross-platform validation.

### Formatting the handbooks

The handbooks carry no screenshots: a picture of a menu that has since
changed misleads more than it helps. Structure carries the reading instead.
These conventions apply to chapters as they are reworked; the SysOp
handbook's "Accounts, permissions, and identity" chapter is the model.

| Element | Write | Renders as |
| --- | --- | --- |
| Menu path | `**Users ▸ Held names**` | **Users ▸ Held names** |
| Hotkey with its label | `<kbd>L</kbd> **Level**` | <kbd>L</kbd> **Level** |
| A key on its own | `<kbd>Esc</kbd>` | <kbd>Esc</kbd> |

- Write labels as the console draws them, without the hotkey brackets.
  `tests/test_handbook_menu_names.py` checks every menu path and every
  hotkey-with-label in these notations against the source, so a renamed
  screen fails the test suite instead of going stale. Text in the older
  notation (`→`, `[N]ame`) is not checked.
- Start a chapter with a short table: where it lives in the console, its
  shell commands, and the decision to make first.
- Write a procedure as numbered steps, with its explanation after them, and
  a set of rules or effects as a bulleted list rather than a paragraph.
- Use GitHub alerts sparingly, at most a few per section:
  `> [!NOTE]` for background worth knowing, `> [!TIP]` for a shortcut or a
  good default, `> [!IMPORTANT]` for what a step needs to succeed,
  `> [!WARNING]` for what can lock someone out or give away too much, and
  `> [!CAUTION]` for what cannot be undone.
- Fold rationale and rare cases into `<details>` with a `<summary>` that
  says what is inside.
- Give a task its own `####` heading named for the task ("Reset a
  password"), so the page outline finds it.
