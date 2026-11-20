# Weekly website routine — analysis checklist

This is the **judgement** half of the routine: `sitepulse audit` measures, this checklist
decides *what is worth doing*. It applies to any site, published yesterday or years old.

Golden rule: **each week produces at most 3 actions per site.** A list of 20 things
never gets done. Three do.

---

## How to read the score

`sitepulse audit` gives a 0–100 score per site and per area (technical, seo, content,
conversion, security). The absolute number matters little: **what matters is the Δ
compared with last week**.

| Situation | How to react |
|---|---|
| Negative Δ | it is a **regression**: something that worked has broken. Top priority, always. |
| New `PROBLEM` | it goes into the week's 3 actions. |
| Same `WARNING` for 4+ weeks | either fix it now or declare it "accepted" and stop reporting it. |
| All green, Δ zero | a **growth** week, not a maintenance week: go to "If there is nothing to repair". |

---

## 1. Technical — the site is there and answers

From the data: `online`, `response-time`, `page-weight`, `broken-links`, `page-404`,
`mobile-viewport`, `broken-resources`, `external-dependencies`, `image-dimensions`.

To assess by hand:

- **Is the site offline or erroring?** Everything else takes second place.
- **Broken links**: a broken internal link is our fault → fix it immediately. A broken
  external link is content that has aged → update it or remove it.
- **Weight above 2.5 MB**: it is almost always the images. Order of intervention: convert
  to WebP → reduce the real dimensions → `loading="lazy"` below the fold.
- **External dependencies** (CDNs): every script from another host is a point of failure
  and a slowdown you do not control. If a library can be served from the site, serve it
  from the site.
- **A 404 that answers 200**: search engines index ghost pages. Fix it in your hosting
  configuration.

## 2. SEO — people get here from a search engine

From the data: `title`, `meta-description`, `lang`, `canonical`, `h1`, `heading-structure`,
`structured-data`, `social-sharing`, `robots`, `sitemap`.

To assess by hand:

- **Does the title say what you offer, or only who you are?** "Studio X" brings nobody.
  The title must contain the problem you solve, in the words the customer would use.
- **Does the meta description invite a click**, or is it a bureaucratic description?
- **One page per question.** If the site is a single page covering 6 topics, a search
  engine does not know what to show it for: growth comes from pages each dedicated to
  one question.
- **Is the structured data consistent with the site?** `LocalBusiness` or
  `ProfessionalService` for those who sell services, `FAQPage` where there are questions
  and answers, `Article` for content.
- **A sitemap with only 1 URL** = the site is a single page. It is not an error, it is a
  ceiling on growth: report it as such.

## 3. Content — there is something to read and it looks alive

From the data: `text-amount`, `image-alt`, `stale-years`, `last-modified`, `favicon`.

To assess by hand:

- **Old years in the text** ("updated in 2024"): they make the site look abandoned.
  Remove them or update them. It is the cheapest defect to fix and one of the most lethal.
- **Regulatory references or prices**: they change. If the site quotes any, they must be
  verified.
- **Untouched for over 90 days**: decide whether the site is "finished" (and that is
  fine) or abandoned (and then it needs one new piece of content a month, no more).
- **Does the first screen say who the site is for?** A visitor must understand within 5
  seconds whether they are in the right place. If they do not, everything else is wasted.

## 4. Conversion — the visitor does what you want

From the data: `contact-channels`, `call-to-action`, `analytics`, plus the site's `goal`
field in `sites.json`.

To assess by hand:

- **Compare each page with its declared `goal`.** If the goal is "a phone call", the
  number must be visible without scrolling and be clickable (`tel:`).
- **Friction in the form**: every extra field lowers submissions. Name + contact + one
  line of text are almost always enough.
- **No visit measurement** = you are working blind. Before optimising anything, install
  a privacy-respecting counter (for example Plausible or Umami).
- **One main action per page.** Three different buttons of equal importance = none
  gets pressed.

## 5. Security and privacy

From the data: `https`, `security-headers`.

To assess by hand:

- **Missing headers** are added once in your hosting or server configuration and hold
  forever: `X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`,
  `Content-Security-Policy`.
- **API keys in browser code**: check at every review. If the site calls a paid service,
  the key lives in a server-side function, never in public JavaScript.
- **Forms and personal data**: if you collect contact details you need a line of privacy
  notice and a legal basis. This applies to a small site too.

---

## If there is nothing to repair

The weeks when everything is green are the ones that make the site grow. In order of
return for the effort:

1. **A new page that answers a real question** from customers (a question actually
   received, not an imagined one). It is the most reliable way to get new visits.
2. **Social proof**: a case, a number, a real photo, a testimonial. It raises conversion
   without touching traffic.
3. **Rewrite the first screen** to be more direct and concrete.
4. **Reduce friction**: one field fewer in the form, one step fewer to get in touch.
5. **Link the pages to each other** with sensible internal links: it helps search engines
   and readers.
6. **External presence**: a business listing, an industry directory, a profile where
   customers already search. It is worth more than a technical micro-optimisation.

## What NOT to do in this routine

- Do not redo the design because "we don't like it any more": the cost is high and the
  return uncertain.
- Do not chase a 100/100 score: the last points cost more than they are worth.
- Do not add tools (chat, popups, banners) without a measured problem to solve.
- Do not work in depth on more than one site in the same week.

---

## Expected report format

For each site, in plain language:

1. **Status in one line** — "it stands and it works", "there is a regression", "it is idle".
2. **What changed** since last week (real changes only).
3. **3 priority actions**, each with: what to do, why it is worth it, what it costs
   (minutes/hours) and how you can tell it worked.
4. **One thing to leave alone**, if there is one: things flagged by the data that are
   not worth the effort.

At the end of the report, a section **"the week in three lines"** across all sites: the
most urgent thing, the most profitable thing, the thing to ignore.
