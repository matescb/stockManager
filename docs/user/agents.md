# Connect an AI assistant

Audience: end user

Let an AI assistant work in this workspace — look parts up, check what you are short of, and, if you allow it, add parts and record stock.

Stock Manager speaks the Model Context Protocol (MCP), which is how assistants such as Claude connect to an outside system. You paste one address and one token into your assistant, and it can then use your workspace the way you would.

## What an assistant can do

Reading, always:

- Find a part by part number, name, manufacturer or description, and read everything known about it — specifications, supplier data, stock by location, whether it has CAD data.
- Report stock: how many are on hand, how many are committed to open builds, how many are actually free.
- List your storage locations, categories and projects.
- Read a project's bill of materials, and work out what you are short of to build it.
- Say which parts still have no symbol, footprint, 3D model or simulation model.

Writing, only with a full-access token:

- Create a part, file it under a category, and record its specifications.
- Add, consume and move stock.
- Create a category.
- Attach CAD data — upload a symbol, footprint, 3D model or simulation file, import a supplier zip, or fetch from LCSC. See [Use your parts in KiCad](kicad.md).
- Look up distributor prices and availability. This one counts as writing even though it only reads, because it spends your workspace's metered distributor quota.

Everything the assistant does is recorded in the workspace history under **your** name. The token acts as you.

## 1. Mint a token

1. Open **Settings → API tokens**.
2. Click **Create API token**.
3. Give it a name you will recognise later ("Claude on the workshop laptop").
4. Tick **Read-only** if the assistant should only look, not touch. See [Read-only or full access](#read-only-or-full-access) below.
5. Leave the expiry blank for a token that never expires, or set a number of days.
6. Click **Create**, and **copy the token now**. It is shown once and cannot be recovered. If you lose it, revoke it and make another.

> _Screenshot: the API tokens page with the create dialog open and Read-only ticked._

The token belongs to you and to the workspace you were in when you made it.

## 2. Point the assistant at your workspace

Open **Settings → KiCad setup** and scroll to the **Not KiCad — AI agents** card. It shows the address to use. Copy it.

Then tell your assistant about the server. Most clients keep a configuration file that looks like this:

```json
{
  "mcpServers": {
    "stockmanager": {
      "type": "http",
      "url": "https://<HOST>/mcp",
      "headers": {
        "Authorization": "Bearer smk_3f1c…b9.KJ3n…Qw"
      }
    }
  }
}
```

Claude Code adds the same thing from the command line:

```bash
claude mcp add --transport http stockmanager https://<HOST>/mcp \
  --header "Authorization: Bearer smk_3f1c…b9.KJ3n…Qw"
```

`Token` works in place of `Bearer` if your client prefers it.

**Paste the token itself, not the name of a variable.** Writing `Bearer ${STOCKMANAGER_TOKEN}` looks tidy, but many clients send header values exactly as written and never replace the variable. Stock Manager then receives the literal text `${STOCKMANAGER_TOKEN}`, sees a token that does not exist, and refuses the connection. If your client has its own way of reading secrets from the environment, use that; otherwise paste the real token.

Ask the assistant something simple to check it worked — "how many 10k 0603 resistors do we have?" is enough.

## Read-only or full access

| | Read-only token | Full-access token |
|---|---|---|
| Look things up | Yes | Yes |
| Add parts, stock, categories, CAD files | No | Yes |
| Look up distributor prices | No | Yes |
| If the token leaks | Someone can read this workspace | Someone can change this workspace |

Start read-only. It is the right choice for a research assistant, for anything running unattended, and for any token that ends up in a file on a laptop. Mint a full-access one when you actually want the assistant to file parts for you, and revoke it when that job is done.

A read-only token connects normally and can still see the full list of things it could do. It is refused only at the moment it tries to change something, and the refusal says so plainly. That is deliberate: hiding the write actions would teach the assistant the feature does not exist, and it would then tell you Stock Manager cannot do something it can.

You can change your mind at any time. **Settings → API tokens** lists every token, when it was last used, and a **Revoke** button. Revoking takes effect immediately.

## What the assistant will refuse

These are normal answers, not faults. If the assistant reports one, it did what it was told and the workspace said no.

**"That part number already exists."** Every manufacturer part number belongs to one part in a workspace. Asking to add a part that is already there does not create a second one — the assistant is handed the existing part instead. Read its reply carefully: it will say it found the part rather than added it.

**"This part requires its default storage location."** Some parts are set up so stock can only go in one bin. Adding stock anywhere else, or nowhere in particular, is refused. Tell the assistant which location to use, or change the part's storage rules on its page. See [Storage locations](storage.md).

**"No such part / category / location in this workspace."** The token is pinned to one workspace and cannot be moved to another. Anything belonging to a different workspace simply does not exist as far as the assistant is concerned, and it is told "not found" rather than "not allowed". If you have more than one workspace, mint a separate token in each.

**"This tool writes and the token is read-only."** Exactly what it says. Mint a full-access token if you want the assistant to make the change.

**"Requires role member."** Your own permissions still apply. A viewer's token is a viewer's token however it was made. See [Workspace members and roles](workspace-management.md).

**"Rate limit exceeded."** The assistant is going too fast. Each kind of action has a ceiling per workspace. Wait the number of seconds it names and continue.

The assistant is also stopped from touching specifications that came from a supplier. It can add its own and update ones it wrote, but a value DigiKey or Mouser supplied is left alone and reported back untouched. Edit those on the part's page, where the app records that a person took ownership of the value.

## What to do if it doesn't work

**The assistant says it is not authorised, or the connection fails straight away.**
Check the header first. The most common cause by far is a configuration file containing a variable name that the client never replaced — look for `${` in the value and replace the whole thing with the token itself. Then check the token still exists in **Settings → API tokens** and has not been revoked or expired. Stock Manager deliberately gives the same answer for every bad credential, so the message will not tell you which of these it was.

**The assistant connects but finds nothing.**
The token is pinned to the workspace you were in when you minted it, which may not be the one you are looking at now. Check the workspace switcher in the user menu, then mint a token from the workspace you actually mean.

**The assistant says it cannot add anything.**
Either the token is read-only, or your own role in this workspace is viewer. The refusal says which. Mint a full-access token for the first; ask an admin to raise your role for the second.

**The assistant lists tools but says the server is not available.**
An administrator can switch the whole assistant interface off. When that happens the address stops answering entirely, and the **Not KiCad — AI agents** card disappears from **Settings → KiCad setup**. Ask your admin.
