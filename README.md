# UKRAINE FAMILY — HTTPS test server

Server extracted from the UKRAINE FAMILY 8.0 source bundle. Compatible with protocol 8. Python standard library and SQLite; no external Python packages required.

## Render test deployment

The included render.yaml selects a Free web service in Frankfurt. Render supplies the public HTTPS certificate and address. The process binds to 0.0.0.0:$PORT. Caddy and a purchased domain are not required for this deployment.

**This is a temporary test server. Accounts and progress are lost when the service restarts, redeploys, or spins down.** The database is /tmp/family.sqlite3. Free services sleep after inactivity; the first connection can take around one minute. This is not a permanent public game server.

The service uses the existing protocol without adding WebSocket. It simulates player movement, vehicle control, purchases and courier rewards on the server. It does not implement the full planned RP game.

### Check and connect

Open https://YOUR-SERVICE.onrender.com/health. Expected JSON:

```json
{"ok": true, "version": "8.0", "protocol": 8}
```

In the Android game, enter the base HTTPS address **without /health**. Register separate player accounts on two devices. Usernames contain 3–24 Latin letters, digits or underscores; passwords contain 14–128 characters. Sessions currently expire after 15 minutes; log in again when requested.

No owner account or password is embedded in this repository. Self-registration creates players only. For durable deployment, attach persistent storage on a suitable paid plan and change --db to a path on that storage before creating the owner. On a persistent host, create the owner interactively:

```sh
python server/server.py --db /PATH/ON/PERSISTENT/DISK/family.sqlite3 create-user Ogoneck --role owner
```

Add the returned TOTP secret to your authenticator and keep it private. Do not post credentials, database files or signing keys to GitHub.

### Tests and local development

```sh
python -m unittest discover -s server -q
python server/server.py --db /tmp/family-dev.sqlite3 serve --bind 127.0.0.1 --port 8765
```

Local HTTP is for development only; Android requires trusted HTTPS. The original 71 server tests cover authorization, moderation, game actions, chat, persistence and proxy validation. A passing suite is not a capacity or Android compatibility guarantee.

The server accepts client-IP overrides only from explicitly trusted proxies. This Render configuration leaves that trust list empty. Source-IP limits can consequently be shared by players behind the same Render proxy; this test setup is intended for a small compatibility check.
