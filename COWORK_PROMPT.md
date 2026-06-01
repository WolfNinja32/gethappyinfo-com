# Claude Cowork Deployment Prompt

## Task: Deploy Open Graph Tags Update to Production

### Background
We've implemented server-side Open Graph tag injection for gethappyinfo.com. This allows each archive date (e.g., `/2026-05-27`) to show a custom social media preview with that day's title and task, rather than the generic homepage preview.

**What was changed:**
- New Cloudflare Worker script (`src/index.ts`) that injects OG tags server-side
- Updated `public/index.html` with enhanced OG meta tags
- Facebook page link added to OG tags (`og:see_also`)
- Dependencies configured (`package.json`, `tsconfig.json`)

**Branch:** `claude/gethappyinfo-og-tags-tu01H`

---

## Your Task

Deploy the changes from branch `claude/gethappyinfo-og-tags-tu01H` to production.

### Steps to Complete

1. **Checkout the branch**
   ```bash
   git fetch origin claude/gethappyinfo-og-tags-tu01H
   git checkout claude/gethappyinfo-og-tags-tu01H
   ```

2. **Install dependencies**
   ```bash
   npm install
   ```

3. **Authenticate with Cloudflare** (one-time setup)
   ```bash
   npx wrangler login
   ```
   - This opens your browser for OAuth authentication
   - No additional configuration needed

4. **Test locally (optional but recommended)**
   ```bash
   npm run dev
   ```
   - Visit `http://localhost:8787/2026-05-27` in your browser
   - Verify the page loads correctly
   - Press `Ctrl+C` to stop

5. **Deploy to production**
   ```bash
   npm run deploy
   ```
   - Waits for confirmation that deployment is complete
   - Should show success message from Wrangler

### Verification

After deployment, verify the changes are live:

1. Visit `https://gethappyinfo.com/2026-05-27` (or any archive date)
2. Open [Facebook Sharing Debugger](https://developers.facebook.com/tools/debug/)
3. Paste the archive URL
4. Confirm you see:
   - **Custom title:** "Get Happy Info — MAY 27, 2026"
   - **Custom description:** That day's task
   - **og-image.png:** The preview image

**Expected result:** Each archive date shows a unique preview instead of the generic homepage preview.

---

## Key Files

- `src/index.ts` — Worker script that handles OG tag injection
- `public/index.html` — Updated with OG meta tags and Facebook link
- `DEPLOY.md` — Detailed deployment guide
- `wrangler.jsonc` — Cloudflare Workers configuration
- `package.json` — Dependencies and deployment scripts

---

## Rollback Plan

If issues occur, rollback is simple:
```bash
git checkout main
npm run deploy
```

---

## Success Criteria

✅ Deployment completes without errors  
✅ Archive dates show custom social media previews  
✅ Homepage still loads correctly  
✅ Facebook Sharing Debugger shows correct OG tags for archive dates
