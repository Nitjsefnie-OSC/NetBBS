# Issue #1354 — the New Year and clock changes in chat — decided

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

The midnight announcement (`netbbs.net.daybreak`, "Good morning, chatters! A
new day has just begun: ...") gains two variants, both sent the same way:
only into channels with someone in them, local to this node, and kept in
scrollback as kind `daybreak`.

- **New Year.** On January 1st the message opens "Happy New Year, chatters! A
  new year has just begun:" and gives the date as on every other day.
- **Clock changes.** At the moment the node's display timezone changes its
  UTC offset, the announcer posts, for example, "Clocks just went forward an
  hour: where this node lives, it is now 03:00 CEST."
  - **Any zone.** The trigger is the offset of the node's own IANA zone,
    not a fixed CET/CEST rule, so a node in New York says EDT/EST and one in
    a zone without DST never says anything.
  - **Wording from the data.** Direction and amount (an hour, half an hour)
    come from the offset difference, and the abbreviation from the tz data.
    The message never says "summer" or "winter time", because tzdata models
    a few zones (Ireland) with the reverse sign.
  - **At the moment it happens**, usually 02:00 or 03:00 local. A note folded
    into the next midnight message was considered and dropped: scrollback
    should show the change when it happened.
- **One schedule.** Before sleeping to midnight, the announcer looks for an
  offset change before then, found to the second by bisection (a zone
  changes at most once in a day). It wakes for whichever comes first. It
  also never schedules before the moment it last announced, so a sleep that
  wakes a hair early cannot announce the same midnight twice. A zone that
  changes at midnight itself (Santiago, Havana) gets that day's message
  first and the clock change right after it.
- **No setting.** The daily message has none, and these follow it.
