# YouTube cookies for Bucket-HUB (Proton)

YouTube blocks many VPN exit IPs. Hub downloads go through **Gluetun → Proton**, so yt-dlp/MeTube need a **Netscape cookies.txt** from a signed-in YouTube session.

## Install on Ubuntu (`ai-server`)

```bash
mkdir -p ~/homelab/data/ytdlp
chmod 700 ~/homelab/data/ytdlp
# copy cookies.txt here (mode 600)
chmod 600 ~/homelab/data/ytdlp/cookies.txt
```

Path expected by Hub worker / MeTube:

`/home/bucket/homelab/data/ytdlp/cookies.txt`

## Export (Firefox)

1. Sign in to YouTube in Firefox (preferably while using Proton, or accept re-export after IP change).
2. Use a cookies export extension that writes **Netscape HTTP Cookie File** format, or follow:
   https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies
3. Include `youtube.com` / `.youtube.com` cookies.
4. Copy the file to the path above (never commit it to git).

## After updating cookies

```bash
cd ~/homelab && docker compose up -d metube
systemctl --user restart hub-worker
```

Hub status should show YouTube cookies as present. Re-export if downloads start failing with bot / sign-in errors again.
