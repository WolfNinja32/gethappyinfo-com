# Deploy Open Graph Tags Update

## Overview
This updates gethappyinfo.com to show custom social media previews for each archive date (e.g., `/2026-05-27`). The changes include a new Cloudflare Worker script that injects Open Graph meta tags server-side.

---

## Prerequisites
- Node.js installed (v16+)
- Git access to the repository
- Cloudflare account with admin access to gethappyinfo.com
- Terminal/command line access

---

## Step-by-Step Deployment

### 1. Clone/Update the Repository
```bash
git clone https://github.com/WolfNinja32/gethappyinfo-com.git
cd gethappyinfo-com
git fetch origin
git checkout claude/gethappyinfo-og-tags-tu01H
```

### 2. Install Dependencies
```bash
npm install
```
This installs:
- `wrangler` (Cloudflare Workers CLI)
- `typescript` (for compiling worker code)
- `@cloudflare/workers-types` (type definitions)

### 3. Authenticate with Cloudflare
```bash
npx wrangler login
```
- Opens your browser automatically
- Click "Allow" to authorize
- Returns to terminal when complete

### 4. (Optional) Test Locally
```bash
npm run dev
```
- Starts local dev server at `http://localhost:8787`
- Test: Visit `http://localhost:8787/2026-05-27` in your browser
- Check that the page loads correctly
- Press `Ctrl+C` to stop

### 5. Deploy to Production
```bash
npm run deploy
```
- Compiles TypeScript
- Uploads worker code to Cloudflare
- Deploys globally within ~30 seconds
- Shows deployment status in terminal

---

## Verify Deployment

**Check that it worked:**

1. Visit: `https://gethappyinfo.com/2026-05-27` (or any archive date)
2. Go to: https://developers.facebook.com/tools/debug/
3. Paste the URL above
4. You should see:
   - **Title:** "Get Happy Info — MAY 27, 2026"
   - **Description:** That day's task/message
   - **Image:** The og-image.png preview
5. If you see the custom preview, deployment was successful ✓

---

## What Changed

### Files Added
- `src/index.ts` — New Cloudflare Worker script
- `package.json` — Dependencies and scripts
- `tsconfig.json` — TypeScript configuration

### Files Modified
- `public/index.html` — Added OG meta tags + Facebook page link
- `wrangler.jsonc` — Updated to use the new worker

### What It Does
- Intercepts requests for archive dates (`/YYYY-MM-DD`)
- Fetches data from `joy-archive.json`
- Injects custom OG tags with that day's info
- Caches for 1 hour for performance
- Falls back gracefully if data unavailable

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `npm install` fails | Delete `node_modules/` and `package-lock.json`, then try again |
| `wrangler login` doesn't open browser | Copy the URL from terminal and paste in your browser manually |
| `npm run dev` fails | Ensure Node.js v16+ is installed: `node --version` |
| Deploy says "not authenticated" | Run `npx wrangler login` again |
| Archive dates still show old preview | Wait 5-10 minutes for global cache to refresh, or hard refresh browser (Ctrl+Shift+R) |

---

## Rollback (if needed)

If something goes wrong, revert the previous version:
```bash
git checkout main
npm run deploy
```

---

## Questions?

- Check the commit messages for implementation details
- Review `src/index.ts` for the worker logic
- See `public/index.html` for OG tag configuration
