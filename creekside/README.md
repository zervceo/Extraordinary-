# Creekside website

**Creekside: Golf • Food • Fun.** Marketing site built from the approved homepage direction.

Static site (Eleventy) with a browser-based content manager (Decap CMS). No database, no plugins to patch, fast by default.

## Run it

```bash
cd creekside
npm install
npm run dev      # http://localhost:8080
npm run build    # outputs to _site/
```

## Pages

Home · Bays · Mini Golf · Lessons · Food · Events (What's Happening + Your Event. Our Place. + inquiry form) · Visit · 404

## What staff can edit in the CMS (`/admin/`)

| Content | Where |
| --- | --- |
| Hours | Hours, Pricing & Announcements → Hours |
| Pricing (bays, mini golf, lessons) | Hours, Pricing & Announcements → Pricing |
| Homepage announcement bar | Hours, Pricing & Announcements → Homepage Announcement Bar |
| Events and promotions | Events & Promotions (add, edit, unpublish, feature on homepage) |
| Food menu | Food, Mini Golf & Private Events → Food Menu |
| Mini golf info | Food, Mini Golf & Private Events → Mini Golf Information |
| Private event types | Food, Mini Golf & Private Events → Your Event. Our Place. |
| Booking links, phone, address, analytics ID | Site Settings |
| Photos | Media library (uploads to `src/assets/img`) |

Hours are stored once and feed the footer, the Visit page, the "Open now" badge and the search-engine structured data.

## Before launch

1. **Replace placeholders** in `src/_data/site.json`: address, phone, emails, live URL, and the three action links (`booking_url`, `lesson_url`, `order_url`). Until a booking system is connected, Rent a Bay points to the Bays page reservation section.
2. **Confirm hours, pricing and menu.** Current values are samples.
3. **Photography.** Images in `src/assets/img` are low-resolution crops of the mockup. Replace each file (same filename) with real Creekside photos; aim for 1600px+ wide for `hero`.
4. **Redirects.** `src/_redirects` has 301s for common legacy paths. Crawl the current site and add one line per URL that changes.
5. **Analytics.** Add the GA4 ID in Site Settings. Every CTA already emits a `cta_click` event (`cta_name`, `cta_placement`), and the event form emits `form_submit`; mark `rent_a_bay` clicks as a key event in GA4.
6. **Hosting.** Configured for Netlify (`netlify.toml` at repo root): enables the CMS login (Identity + Git Gateway), the event inquiry form and the redirects file. Clear `preview_notice` in Site Settings at launch.
