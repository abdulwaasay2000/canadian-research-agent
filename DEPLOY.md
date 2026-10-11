# Publishing on the web

Two ways to put this online. Both start in **research mode with labelled demo data**. Live trading isn't possible in any of them.

## Option A: the full app (dashboard runs on a server)

### Streamlit Community Cloud (free, easiest)
1. Upload this folder to a new GitHub repository. Leave out `.env` and `data/`.
2. Go to share.streamlit.io, click **Create app**, and pick the repo.
3. Set **Main file path** to `streamlit_app.py`. Under Advanced settings, choose Python 3.12.
4. Optional, for real data: open **Secrets** and add:
   ```
   CRA_DATA_SOURCE = "live"
   SEC_USER_AGENT = "Your Name you@email.com"
   FRED_API_KEY = "..."
   EODHD_API_KEY = "..."
   ```
5. Click **Deploy**. On the first start it loads data, which takes about 30 seconds.

### Render (Docker, free tier)
1. Push the folder to GitHub.
2. In Render, choose **New**, then **Blueprint**, and pick the repo. `render.yaml` sets everything up.
3. Add your keys under **Environment** if you want live data, and set `CRA_DATA_SOURCE=live`.

On Render's free plan the disk resets on every restart, so data reloads at each boot.

### Railway or any Docker host
Railway, Fly.io and any VPS will build the `Dockerfile` and run `start.sh`, which listens on `$PORT`. To run it yourself:

```
docker build -t cra .
docker run -p 8501:8501 cra
```

## Option B: static site (no server)

Use the `research-desk-static-site.zip` folder, or build a fresh one with:
```
python -m cra.build_webapp --out site/index.html
```
Then drag the folder onto app.netlify.com/drop, or upload it to GitHub Pages, Vercel or Cloudflare Pages.

The static site shows the snapshot it was built with. Rebuild and re-upload it to refresh the numbers, or use the **Load snapshot.json** button on the page.

## Before you share the link
- Anyone with the URL can see the page. Keep API keys in the host's secrets settings, never in files.
- The verdicts are research labels from rules that haven't been backtested. The page says so, so leave those notices in.
- Check each data provider's licence before you show its data publicly. EODHD's personal plans don't allow public display.
