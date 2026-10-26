# Putting the SAFE website live

The SAFE site runs on the same OVH VPS as the owner's portfolio, beside it and separate from it. It has its own user, folders, database, services, Nginx sites and certificate. The portfolio's firewall (only Cloudflare reaches ports 80 and 443) and its Cloudflare real-IP list are shared, unchanged.

```
Cloudflare (proxied) ──► Nginx :443 ─┬─ <domain>       ─► Next.js site    127.0.0.1:3100  (safe-web)
                                     │     /media/      ─► /srv/safe/media (from disk)
                                     │     /api/inquiries ─► Django       127.0.0.1:8100  (safe-api)
                                     ├─ www.<domain>   ─► redirect to <domain>
                                     └─ api.<domain>   ─► Django: /api/, /admin/, /static/, /media/
safe-drive-sync.timer  every 15 min: Google Drive ─► /srv/safe/drive_mirror ─► database + media
safe-backup.timer      nightly: database ─► /srv/safe/backups (newest 14)
```

On every push to `main`, GitHub Actions does the rest:

- **API repo**: tests, `makemigrations --check`, `check --deploy`, then upload and `deploy/release-api.sh`.
- **Site repo**: lint and typecheck, a build against the live API, then upload and `deploy/release-web.sh`.

Each release goes into its own folder, and `current` points at the live one. The new release must answer its health check; if it doesn't, the script switches back. The three newest releases are kept.

## Layout on the server

```
/srv/safe/api/releases/<name>/   API code; .venv -> ../../venvs/<hash> (one per requirements.txt)
/srv/safe/api/current            -> the live API release
/srv/safe/web/releases/<name>/   Next.js standalone builds;  /srv/safe/web/current -> the live one
/srv/safe/web/image-cache/       resized pictures, shared by every build
/srv/safe/media/                 project pictures, drawings, videos, logos (public)
/srv/safe/private_media/         clients' attachments (admin only)
/srv/safe/drive_mirror/          copy of the "SES Website Data" Drive folder
/srv/safe/backups/               database copies
/etc/safe/api.env, web.env       settings and secrets (root:safe 640; made by bootstrap.sh)
/etc/safe/drive-sync-key.json    the Google service account key (root:safe 640)
```

## GitHub settings

They live in **both** repos as **repository** secrets and variables: *Settings > Secrets and variables > Actions*. Set them with the script below, not by hand:

| Name | Kind | Value |
|---|---|---|
| `DEPLOY_HOST` | secret | The server's **IPv4 address**, not the domain. The domain goes through Cloudflare's proxy, which doesn't carry SSH. |
| `DEPLOY_SSH_KEY` | secret | The whole private key file `F:\Users\Ben\.ssh\safe_deploy`. |
| `DEPLOY_KNOWN_HOSTS` | secret | The server's host keys, `F:\Users\Ben\.ssh\safe_deploy_known_hosts`. |
| `SAFE_DOMAIN` | **variable** | The bare domain, like `example.com`: no `https://`, `www.` or slash. |

```powershell
gh auth login                      # once
powershell -ExecutionPolicy Bypass -File deploy\set-github-secrets.ps1 -Domain example.com -ServerIp <server IPv4>
```

The script prevents the mistakes made with the portfolio's settings:

- **Repository-level, not environment-level.**
  - The site repo is private, and GitHub's free plan gives private repos no environment secrets.
  - A misspelt `environment:` name silently creates a new, empty environment.
- **Read from files and sent on stdin, nothing pasted.**
  - There are no stray spaces or Windows line endings.
  - You can't swap the public key for the private one.
  - The script checks the key has no passphrase.
- **Host keys are checked.** They are compared with what this PC already trusts for that IP: your own logins and the portfolio's deploy.
- **Both repos at once.** Their values can't drift apart, and the names are read back afterwards.

The workflows check the settings again before connecting, and name the exact problem:

- a missing setting
- a secret saved as a variable, or the reverse
- a domain in `DEPLOY_HOST`
- `https://` or `www.` in `SAFE_DOMAIN`
- a public or passphrase-protected key
- known_hosts without the server's IP

With none of the four set, a run shows a yellow **Not deployed** warning instead of silently passing.

## Setting the server up (once)

1. **Cloudflare.** Add the domain to Cloudflare, then switch the Namecheap nameservers to Cloudflare's.
   - Create an A record for `api`, pointing at the server's IPv4, **Proxied** (orange cloud). The firewall only lets Cloudflare in.
   - Leave `@` and `www` on the old host for now, as **DNS only** (grey cloud). They move in "Switching the site over" below.
   - Set **SSL/TLS** mode to **Full (strict)**.
   - Keep **Always Use HTTPS** off until the whole switch is done, then turn it on.
2. **Secrets.** Run `set-github-secrets.ps1` as above. It prints the bootstrap command with the public key filled in.
3. **Bootstrap.** Copy this folder up from your checkout and run it:
   ```bash
   scp -r deploy ubuntu@<server IPv4>:safe-deploy
   ssh ubuntu@<server IPv4>
   sudo bash safe-deploy/bootstrap.sh <domain> "<contents of safe_deploy.pub>" <Drive folder id>
   ```
   It sets up the following:
   - **User and folders:** the `safe` user and its folders.
   - **Deploy key:** limited to `safe`, with `restrict`.
   - **sudoers rule:** may only restart `safe-api` and `safe-web`.
   - **Database:** the PostgreSQL database `safe`.
   - **Settings:** `/etc/safe/api.env` and `/etc/safe/web.env`, with new secrets that never leave the server.
   - **Services:** the services and timers.
   - **Nginx:** the sites and the certificate for all three names.

   It checks first that each name reaches the server, so a DNS slip doesn't use up Let's Encrypt attempts. It is safe to run again: after changing `nginx/` or `systemd/`, or when the certificate step was skipped.
4. **Google key.** Copy the Google key up:
   ```bash
   scp drive-sync-key.json ubuntu@<server>:
   ssh ubuntu@<server> 'sudo install -o root -g safe -m 640 drive-sync-key.json /etc/safe/; rm drive-sync-key.json'
   ```
5. **Email (optional).** Without it, the alerts live in the admin.
   - The admin home shows "Inquiries (2 new)" for requests not yet followed up.
   - It shows "Drive imports (1 new)" for Drive syncs that loaded something or failed.
   - To also get emails, fill in the `EMAIL_*`, `DEFAULT_FROM_EMAIL` and `INQUIRY_NOTIFY_EMAILS` lines in `/etc/safe/api.env` (`sudo nano /etc/safe/api.env`). Then run `sudo systemctl restart safe-api`.

## First deploy

1. **Deploy the API.** Push the API repo to `main`, or re-run its workflow. It creates the empty tables.
2. **Move the laptop's data.** Do this once, before the Drive sync is turned on.
   - Copy the drive_mirror folder too, so the server doesn't download and re-import everything.
   - On the laptop, in `backend/`:
     ```powershell
     .venv\Scripts\python manage.py dumpdata --natural-foreign --natural-primary -e contenttypes -e auth.permission -e admin.logentry -e sessions -e auth.user --indent 1 -o safe-data.json
     scp safe-data.json ubuntu@<server>:/tmp/
     ```
   - Copy the folders. rsync from WSL, or `scp -r`:
     - `media` → `/srv/safe/media`
     - `drive_mirror` → `/srv/safe/drive_mirror`
   - On the server:
     ```bash
     sudo chown -R safe:safe /srv/safe/media /srv/safe/drive_mirror
     sudo chmod 644 /tmp/safe-data.json && sudo safe-manage loaddata /tmp/safe-data.json && rm /tmp/safe-data.json
     sudo safe-manage createsuperuser
     sudo systemctl enable --now safe-drive-sync.timer
     ```
   - Then turn off the laptop's own sync: `scripts\schedule-drive-sync.ps1 -Remove`.
3. **Deploy the site.** Push the site repo to `main`. Its build reads the content from `https://api.<domain>`. Check it on the server: `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3100/`.

## Switching the site over

Until now `<domain>` and `www` still show the old site. To switch:

1. In Cloudflare, point `@` and `www` at the server instead:
   - Replace the old host's records with one A record each, to the server's IPv4.
   - Set both to **Proxied**.
2. Run the bootstrap again: `sudo bash safe-deploy/bootstrap.sh <domain> "<public key>"`. It adds both names to the certificate and enables the site.
3. Turn on **Always Use HTTPS** in Cloudflare.

Between steps 1 and 2, visitors may see a Cloudflare error page for a minute or two.

## Everyday commands (on the server)

```bash
sudo safe-manage createsuperuser            # any manage.py command, as safe, on the live copy
sudo safe-manage sync_drive                 # check the Drive now
journalctl -u safe-api -n 50                # the API's log (also safe-web, safe-drive-sync, safe-backup)
systemctl list-timers 'safe-*'              # when the Drive sync and backup run next
ls -lh /srv/safe/backups                    # database copies; backup-db.sh says how to restore one
sudo -u safe sh /srv/safe/api/current/deploy/backup-db.sh manual   # a copy now
```

**Switching back by hand.** Point `current` at an older release and restart:
```bash
sudo -u safe ln -sfn releases/<name> /srv/safe/api/current && sudo systemctl restart safe-api
```
That doesn't undo database migrations. Each release that migrated left a `before-<release>` copy in `/srv/safe/backups`.

## Limits worth knowing

- **Upload size.** Cloudflare's free plan refuses any upload over 100 MB.
  - Big videos go through the Drive folder, not the admin.
  - The request form allows 8 files of 25 MB, but over 100 MB in total fails at Cloudflare.
- **Cloudflare bot settings.** The site's build reads the API from GitHub's servers. If Cloudflare's bot protection challenges them, the build stops at "Is the API live?". Allow `api.<domain>/api/` in the WAF.
- **Off-server backups.** The database copies are on the same server. Download one now and then, or turn on OVH's automated backup.
