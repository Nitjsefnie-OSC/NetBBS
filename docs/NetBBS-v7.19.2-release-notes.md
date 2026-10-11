# NetBBS v7.19.2

This patch release tidies the chat-bot screens in the SysOp console and fixes
a picker prompt that could be erased while paging. **Nothing migrates:** the
node database stays at schema 130, and the Link, real-time, door API, career
and world versions are unchanged. Upgrading is a wheel swap and a restart;
rolling back to v7.19.1 is the reverse.

## Shorter bot breadcrumbs (PR #1236)

Bot screens now show the node and the current screen, for example
`Reliable Link > Chat bots`, instead of the full path
`Reliable Link > SysOp > Content > Chat bots`. The bot editor, model settings
and channel and source pickers use the same shorter path. The existing
location-style preference still decides whether the node is shown or the
heading collapses to the current screen alone.

## Instructions below the breadcrumb (PR #1236)

Explanatory text on bot screens now appears below the breadcrumb instead of
above it. A picker's instructions wrap to the terminal and count toward the
page budget for both empty and populated lists. When instructions are present,
the picker uses its generated layout, so custom list art can no longer hide
them.

## Picker prompt survives a boundary key (PR #1236)

Pressing cursor-left on a picker's first page no longer erases the `Choice`
prompt. Paging keys arrive unechoed, so a rejected move past the first or last
page now only rings the bell instead of backspacing over the prompt. This
covers Left/Right, Page Up/Page Down and `<`/`>` in every picker, not only the
bot screens.

## Upgrade and rollback

Stop NetBBS, replace the wheel and start it. No migration runs, and no setting
or config key is added. Rolling back to v7.19.1 is the reverse.

## Verification boundaries

- PR #1236's regression tests cover the breadcrumb format, header order,
  empty and populated catalogues, 40- and 80-column layouts, location
  preferences and repeated paging keys over real loopback Telnet. The
  header-order and prompt-erasure tests fail without their fixes.
- NetBSD, real-terminal and live multi-node checks remain manual evidence;
  this patch does not change those boundaries.
