interface Env {
  ASSETS: any;
}

interface DailyData {
  lastUpdated: string;
  dailyTask: string;
  topNews: Array<{ text: string; url: string }>;
}

interface Archive {
  days: Record<string, DailyData>;
}

async function getArchiveData(env: Env): Promise<Archive | null> {
  try {
    const response = await env.ASSETS.fetch(new Request('https://gethappyinfo.com/joy-archive.json'));
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

function dateParts(iso: string): { year: string; monthShort: string; day: number; iso: string } | null {
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return null;
  const months = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
  return {
    year: m[1],
    monthShort: months[parseInt(m[2], 10) - 1] || '',
    day: parseInt(m[3], 10),
    iso: m[0],
  };
}

function injectOGTags(html: string, title: string, description: string, url: string): string {
  // Escape HTML entities in content
  const escapeHtml = (s: string) =>
    s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');

  const escapedTitle = escapeHtml(title);
  const escapedDesc = escapeHtml(description);
  const escapedUrl = escapeHtml(url);

  // Replace OG meta tags in the head
  return html
    .replace(
      /(<meta property="og:title" content=")([^"]*)/,
      `$1${escapedTitle}`
    )
    .replace(
      /(<meta property="og:description" content=")([^"]*)/,
      `$1${escapedDesc}`
    )
    .replace(
      /(<meta property="og:url" content=")([^"]*)/,
      `$1${escapedUrl}`
    );
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const pathname = url.pathname;

    // Check if this is an archive date request (e.g., /2026-05-27)
    const archiveMatch = pathname.match(/^\/(\d{4}-\d{2}-\d{2})\/?$/);

    if (archiveMatch) {
      const requestedDate = archiveMatch[1];
      const parts = dateParts(requestedDate);

      if (parts) {
        // Fetch archive data
        const archive = await getArchiveData(env);

        if (archive && archive.days && archive.days[requestedDate]) {
          // Date found in archive - inject customized OG tags
          const dayData = archive.days[requestedDate];
          const customTitle = `Get Happy Info — ${parts.monthShort} ${parts.day}, ${parts.year}`;
          const customDesc = dayData.dailyTask || 'One kind act, three pieces of good news. Sent to your screen every morning.';
          const customUrl = `${url.origin}/${requestedDate}`;

          // Get the index.html and inject OG tags
          const indexResponse = await env.ASSETS.fetch(new Request(`${url.origin}/`));
          if (indexResponse.ok) {
            let html = await indexResponse.text();
            html = injectOGTags(html, customTitle, customDesc, customUrl);
            return new Response(html, {
              status: 200,
              headers: {
                'Content-Type': 'text/html; charset=utf-8',
                'Cache-Control': 'public, max-age=3600',
              },
            });
          }
        } else {
          // Date not in archive - serve with "no postcard" OG tags
          const customTitle = `Get Happy Info — ${parts.monthShort} ${parts.day}, ${parts.year}`;
          const customDesc = 'No postcard from that day — yet.';
          const customUrl = `${url.origin}/${requestedDate}`;

          const indexResponse = await env.ASSETS.fetch(new Request(`${url.origin}/`));
          if (indexResponse.ok) {
            let html = await indexResponse.text();
            html = injectOGTags(html, customTitle, customDesc, customUrl);
            return new Response(html, {
              status: 200,
              headers: {
                'Content-Type': 'text/html; charset=utf-8',
                'Cache-Control': 'public, max-age=3600',
              },
            });
          }
        }
      }
    }

    // For all other requests (including /), use default asset handling
    return env.ASSETS.fetch(request);
  },
};
