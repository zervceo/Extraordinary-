# mbtok

Aesthetic mood board videos for TikTok, cut from photos and footage **you already own** — Envato downloads, your camera roll, anything sitting on your Mac.

Point it at your folders. It works out which of your files belong to which aesthetic, picks a set that hangs together, cuts them to the beat of your music, grades them so mixed sources match, and writes the caption and hashtags to go with the post.

No generative model is involved at any stage. Every frame that reaches the output came from a file you supplied.

```bash
mbtok scan ~/Downloads/Envato\ Elements ~/Pictures
mbtok boards                      # which moods does my library support?
mbtok make --preset clean-girl    # render one post
mbtok batch --count 7             # render a week
```

---

## Install

Requires Python 3.10+ and ffmpeg. Nothing else — no Pillow, no numpy, no API keys.

```bash
git clone <this repo> && cd Extraordinary-
./scripts/setup-macos.sh
```

Or by hand:

```bash
brew install ffmpeg
python3 -m pip install -e .
mbtok init ~/Downloads ~/Pictures ~/Movies
mbtok doctor      # confirms everything is in place
```

`mbtok doctor` is the first thing to run if anything misbehaves. It checks ffmpeg, the filters it needs, your fonts, your media folders and your music, and tells you which one is the problem.

---

## How it works

```
  your folders
       |
   [ scan ]        probe every file, extract a 5-colour palette, score it
       |           -> .mbtok/library.json  (cached; rescans are instant)
       |
   [ curate ]      score each asset against the mood, then pick a set that
       |           does not repeat itself
       |
   [ storyboard ]  allocate whole beats to shots so every cut is on the beat
       |
   [ render ]      one ffmpeg pass: Ken Burns, crossfades, grade, grain, text
       |
   [ report ]      caption, hashtags, posting plan, provenance record
       |
    out/*.mp4
```

### Scanning

Every image and clip is probed with `ffprobe` and decoded down to a small RGB grid, from which mbtok extracts a five-colour palette plus brightness, contrast, saturation, warmth and hue spread. Videos are sampled at three points so a clip that changes partway through is not judged on its opening frame.

Results are cached against a cheap file fingerprint, so the slow first pass happens once. Rescanning a library that has not changed takes no time at all.

It also records where each file came from — Envato, Artgrid, Adobe Stock, your camera roll — from the path. That feeds the provenance record attached to every post.

### Curating

Two different problems get solved here.

**Which shots fit the mood.** Each asset is scored on how close its palette sits to the preset's, plus brightness, saturation, contrast, warmth, hue spread, technical quality and whether the frame survives a vertical crop. Every mood weights those differently: Street Mono cares enormously about contrast and not at all about hue, while Vanilla Girl is almost entirely about a tight, bright, low-saturation palette.

**Which set makes a good video.** Eight near-identical beige flatlays all score beautifully and make a terrible board. So each candidate is penalised against what is already chosen, using colour distance reinforced by two structural hints: files in the same folder are usually the same shoot, and files saved within a minute of each other are usually the same burst. Anything above 90% similar to an already-chosen shot is skipped outright unless the library has nothing else to offer.

Shots are then ordered with a shape: the highest-energy frame opens, the second closes, and the middle alternates loud and quiet so the edit breathes.

### Cutting to the beat

Tempo comes from the music itself. mbtok decodes the track to mono, builds an onset envelope from banded energy flux, and finds the tempo by autocorrelation with harmonic reinforcement and sub-frame peak interpolation. On the test suite's click tracks it recovers tempo to within 1% and the first downbeat to within 45 ms. Pass `--bpm` if you already know the tempo, or if the track is ambient enough that detection warns about low confidence.

Cuts are then allocated in **whole beats**, never in seconds that happen to land nearby. The video's length is rounded to a whole number of beats too, so it loops cleanly — which matters, because loops are where this format earns its watch time. Asking for 10 seconds at 104 BPM gets you 9.8 or 10.4, whichever is a whole number of beats.

Because every cut sits at an exact multiple of the beat from zero, the music is started at its own first downbeat rather than at its file start.

### Rendering

One ffmpeg invocation does the whole video.

- **Stills** are pre-scaled to twice the output size, then moved with `zoompan`, so panning happens in source pixels finer than output pixels and the motion is smooth rather than stepped. Six moves are available: push in, pull out, pan left and right, drift up and down.
- **Footage** is seeked past its opening (stock clips usually start on a settling camera), scaled and cropped to fill 1080x1920, and padded with a cloned final frame so a clip a few frames short cannot shorten the timeline.
- **Transitions** are `xfade`, centred on the beat: each starts half a transition before the cut and ends half a transition after it. Every transition is capped at half the shorter of the two shots it joins, so a fast section never crossfades a shot out of existence.
- **The grade** is `eq` plus `colorbalance`, giving each preset its own split-toned cast — shadows one way, highlights the other. This is what makes a phone photo and an Envato clip look like they belong in the same video.
- **Grain and vignette** go on once, over the finished composite, rather than per shot. Crossfading two independently grained shots halves the grain through every transition; a vignette that fades in and out at each cut reads as a mistake.
- **Text** is wrapped and shrunk to fit before it is drawn. `drawtext` neither wraps nor scales, so a long hook is otherwise clipped at both edges — an error you only discover after rendering. Text is passed through a file rather than inline, which sidesteps three layers of filtergraph escaping.

Output is 1080x1920 H.264 in yuv420p with `+faststart`, capped at 14 Mbps. The cap matters: film grain is expensive to encode, and without it a grainy preset at a low CRF produces a file many times larger than a phone will happily upload. TikTok re-encodes on ingest anyway.

---

## The moods

| Key | Look |
| --- | --- |
| `clean-girl` | Milk, linen and morning light. Slicked-back, unfussy, expensive-looking. |
| `vanilla-girl` | Cream knits, candles, cashmere and everything the colour of milk. |
| `old-money` | Tailoring, horses, marble halls and no visible logos. |
| `dark-academia` | Libraries, ink, wool and rain. Candlelight on old paper. |
| `parisian` | Balconies, espresso, and grey stone light. |
| `coastal-linen` | Salt air, bleached wood and white cotton drying in the wind. |
| `cottagecore` | Bread, wildflowers, quilts and a garden that needs weeding. |
| `desert-minimal` | Adobe walls, long shadows, terracotta and heat you can see. |
| `tokyo-night` | Wet asphalt, vending machine glow, neon bleeding into the rain. |
| `y2k-chrome` | Butterfly clips, lip gloss, chrome and a flip phone flash. |
| `soft-grunge` | Grey weather, denim, film scratches and a good sulk. |
| `pilates-princess` | Matcha, reformer studios, ribbed sets and 7am discipline. |
| `street-mono` | Black and white city frames. Contrast, geometry, strangers in motion. |

Each one carries its own palette, grade, pacing, transition set, typography, hook lines, captions, hashtag pools and a note on what music suits it. `mbtok presets -v` prints the details.

`mbtok boards` scores your actual library against all thirteen, so you can see which ones you already have the material for before you commit to a series.

---

## About the AI-content question

This is the reason the tool exists, so it is worth being exact about what is and is not being claimed.

**What is true by construction.** mbtok contains no generative model and calls no generative service. It scales, crops, moves, grades, blends and cross-fades files you supply. There is no step at which a pixel can be invented, so there is nothing for you to disclose under TikTok's synthetic-media policy, and no reason for a C2PA-style AI content credential to be attached to the output.

Every render writes a `*.provenance.json` next to the video listing every source file that contributed a frame, where it came from, and exactly when it was on screen. There is a `licence_reference` field on each entry for your Envato licence code. Months later you can answer "where did this footage come from" for any frame of any post without guessing.

**What is also worth knowing.** The duplicate-content problem is a real one and it is separate from AI detection. Reposting the same clips every few days is a reliable way to get a series quietly deprioritised. mbtok keeps a usage ledger and will not reuse an asset until it has rested — 21 days by default, `--cooldown` to change it. A batch of seven posts draws from seven disjoint sets of footage.

**What no tool can promise.** Nobody outside the platform knows how its classifiers behave, and they change. What this tool guarantees is the input side: no synthetic media, a documented source for every frame, and no repetition. It cannot guarantee any particular platform's judgement, and you should be sceptical of anything that says it can. The presets stay deliberately conservative with warping and heavy stylisation for the same reason.

**Rights are still yours to hold.** mbtok labels sources from the file path; it cannot verify a licence. Envato Elements licences cover this use, but they are per-item and tied to your subscription, and stock licences generally do not permit stock footage to be the *entire* substance of a post you monetise. Check your own licence terms. The provenance file is there to make that easy, not to substitute for it.

---

## Commands

| Command | What it does |
| --- | --- |
| `mbtok init [folders...]` | Create the project config, guessing sensible folders |
| `mbtok doctor` | Check ffmpeg, filters, fonts, folders and music |
| `mbtok scan [folders...]` | Index your media (incremental after the first run) |
| `mbtok presets [-v]` | List the moods |
| `mbtok boards [--preset K --show]` | Score your library against every mood |
| `mbtok make` | Render one post |
| `mbtok batch --count 7` | Render several, sharing one cooldown |
| `mbtok status` | What you have posted, what is resting |

Useful flags on `make` and `batch`:

```
--preset KEY        mood to build
--duration 10       target length, rounded to whole beats
--music track.mp3   audio to cut to
--bpm 104           exact tempo, skipping detection
--shots 8           override the shot count
--cooldown 0        allow assets to repeat
--min-quality 0.2   accept lower-quality sources
--diversity 0.8     push harder for variety
--no-text           render without the hook and sign-off
--dry-run           print the ffmpeg command without running it
```

`--dry-run` prints the exact command. If a render looks wrong, that is the fastest way to see why.

---

## What you get per post

```
out/
  20260910-clean-girl.mp4               1080x1920, H.264, beat-cut
  20260910-clean-girl.caption.txt       caption + hashtags, ready to paste
  20260910-clean-girl.json              full record: storyboard, timings, copy
  20260910-clean-girl.provenance.json   every source file and when it appeared
  posting-plan.md                       one readable plan for the whole batch
```

The rendered audio is a guide track. Swapping to a trending sound inside TikTok is usually worth more reach than the mix you exported with — the posting plan says so too, along with the suggested posting window for each post.

---

## Project layout

```
mbtok/
  color.py       palette extraction, Lab distance, aesthetic metrics
  library.py     scanning, indexing, quality scoring, caching
  presets.py     the thirteen moods: palette, grade, pacing, typography, voice
  curate.py      scoring, diverse selection, shot ordering
  audio.py       onset detection, tempo estimation, beat grids
  storyboard.py  whole-beat cut allocation and the timing model
  render.py      ffmpeg filtergraph construction
  typeset.py     text measurement, wrapping and shrink-to-fit
  captions.py    hooks, captions, hashtag mixes
  ledger.py      usage history and the repeat cooldown
  report.py      provenance records and posting plans
  pipeline.py    orchestration
  cli.py         the command line
```

## Tests

```bash
make test        # everything, including real ffmpeg renders
make test-fast   # skip the ffmpeg integration tests
```

279 tests. The integration tests build a small library of real files, render it, and probe the result — they are the ones that prove the emitted filtergraph is something ffmpeg will actually accept. They skip themselves automatically if ffmpeg is not installed.
