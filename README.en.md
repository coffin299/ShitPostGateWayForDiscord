# ShitPostGateWayBOT

A Discord gateway bot for people who belong to multiple communities and are tired of pasting the same X / pixiv / Instagram link into every server.

One `/shitpost` posts a fixlinked URL locally and fans it out across your configured **send/receive mesh**.

> Japanese README: [README.md](./README.md)

## Features

- `/shitpost` — fixlink (X / pixiv / Instagram), post here, forward to mesh (`silent` option)
- `/fixlink` — convert URL to fixlink and post here (no forward; `link fixed via` attribution)
- `/shitposting_router` — add a one-way route via dropdowns
- `/shitposting_router_mesh` — connect channels into a bidirectional send/receive mesh
- `/shitposting_router_mesh_add` — add channels to an existing mesh
- `/shitposting_router_remove` — remove one destination by channel ID
- `/shitposting_router_remove_all` — bulk-delete mesh routes
- `/show_settings` — your send/receive mesh on this server
- `/show_settings_admin` — [admin] every user's mesh (Components V2 + paging)
- `/reload_config` — reload `config.yaml`, `i18n.yaml`, and `routes.json`
- `/help` — Components V2 help (locale-aware)

Post body example:

```text
-# ShitPostGateWayBot shared by [username](<https://discord.com/users/123456789012345678>)
https://fxtwitter.com/...
```

`/fixlink` example:

```text
-# link fixed via [username](<https://discord.com/users/123456789012345678>)
https://fxtwitter.com/...
```

The username is a profile URL link (not an `@` mention — no ping). Profile URLs are wrapped in `<>` to suppress their embed while keeping the fixlink preview. Use `/shitpost` `silent:true` to suppress push notifications (default is normal).

## Setup

1. Create a bot in the [Discord Developer Portal](https://discord.com/developers/applications) and copy the token
2. Invite the bot to every server you want to forward to (needs send-message permission)
3. Run `start.bat` in this repo (creates `.venv` and installs deps on first run)
4. Edit `config.yaml` → set `token`, save
5. Run `start.bat` again

Manual start:

```powershell
$env:PYTHONDONTWRITEBYTECODE=1
.\.venv\Scripts\python.exe -m bot
```

## Config files

| File | Role |
| :--- | :--- |
| `config.default.yaml` | Template (committed) |
| `config.yaml` | Token, fixlink, permissions (auto-copied, gitignored) |
| `i18n.default.yaml` | JA/EN command descriptions + UI strings (committed) |
| `i18n.yaml` | Runtime copy you can edit (auto-copied, gitignored) |
| `routes.default.json` | Empty routes template |
| `routes.json` | Live mesh edges (written by commands, gitignored) |

### Language

- Slash command descriptions come from `i18n.yaml` (**Japanese is the Discord default**; English via `en-US` / `en-GB` localizations)
- Runtime replies use `interaction.locale` (Japanese → ja, otherwise en)
- discord.py 2.x uses a `Translator` during command sync
- After editing strings: `/reload_config`, then restart the bot to re-sync slash metadata

Routing is edge-based. For bidirectional mesh, mutual edges are created for you. Same-server channels are allowed. Each destination stores `added_by` (user ID) and `added_by_name` (username). When someone runs a slash command, any stored `added_by_name` for their ID is updated in `routes.json` if their username changed.

## Command notes

### `/shitpost`

Posts in the current channel, then forwards to destinations registered for that channel as a source. The count reply is ephemeral.

- `silent` — `true` = no push notifications; `false` (default) = normal notifications

- Skips servers the bot is not in / channels it cannot resolve
- **NSFW sources only forward to NSFW destinations**
- Route/mesh pickers also list **NSFW channels only** when the run channel or chosen source is NSFW

### `/fixlink url:`

Converts the URL to a fixlink domain and **posts it publicly in this channel**. No mesh forward. Attribution is `-# link fixed via [username](<profile>)` (same profile-URL embed suppression as `/shitpost`). Uses the same permission gate as `/shitpost` (`shitpost_role_ids`).

### Mesh commands

- `/shitposting_router_mesh` — pick N channels (2+); they all forward to each other
- `/shitposting_router_mesh_add` — run inside a mesh member channel to join more channels
- Server pickers only list guilds **you and the bot both share**

### `/show_settings` / `/show_settings_admin`

- Personal view: only routes you added (`added_by`)
- Admin view: all users, name + ID, Components V2 paging
- Owner or Administrator required for admin command

## Default fixlink map

| From | To |
| :--- | :--- |
| twitter.com | fxtwitter.com |
| x.com | fixupx.com |
| pixiv.net | phixiv.net |
| instagram.com | ddinstagram.com |

Override via `config.yaml` → `fixlink`.

## Notes

- `config.yaml` / `i18n.yaml` / `routes.json` / `.venv` are not committed
- `start.bat` messages are English (Windows console encoding)
