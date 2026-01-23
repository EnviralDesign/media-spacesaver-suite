Nice — that help dump confirms you’ve got a modern HandBrakeCLI with all the knobs you care about (notably --hdr-dynamic-metadata and the language selectors).

Here’s what matters for your plan, and what you can ignore.

You can ignore 95% of that help output

For “4K movie → smaller HEVC → keep HDR → keep English audio/subs only”, you’ll mostly use:

-i, -o (input/output)

-e x265_10bit (CPU HEVC 10-bit)

--encoder-preset (speed vs compression)

-q (quality level; main size control)

--audio-lang-list eng --first-audio (English audio)

--subtitle-lang-list eng --first-subtitle (English subs)

--hdr-dynamic-metadata ... (optional; only if your sources have HDR10+ or Dolby Vision metadata)

Everything else is edge-case.

The one HDR-related flag you should know about

You already said “keep HDR.” There are two “HDR things”:

1) HDR10 (static metadata)

If the source is HDR10, x265_10bit generally preserves HDR10 fine without special flags (you’re keeping 10-bit and not tone-mapping).

2) HDR dynamic metadata (HDR10+ / Dolby Vision)

That’s what this option is for:

--hdr-dynamic-metadata hdr10plus

--hdr-dynamic-metadata dolbyvision

--hdr-dynamic-metadata all

Reality check: DV handling is the one that gets messy across toolchains/devices. A pragmatic default is:

preserve HDR10+ if present

skip or special-case DV titles until you’ve tested your playback path

Your “golden test command” (CPU, HDR-safe baseline, English-only)

Run this on one file:

C:\handbrakecli\HandBrakeCLI.exe `
  -i "X:\path\to\movie.mkv" `
  -o "X:\path\to\movie__x265_test.mkv" `
  -f av_mkv `
  -e x265_10bit `
  --encoder-preset slow `
  -q 20 `
  --audio-lang-list eng --first-audio -E copy `
  --subtitle-lang-list eng --first-subtitle


Notes:

-q 20 is a solid starting point for 4K. Smaller files: try 21–22.

-E copy passes audio through (keeps original codec). If you later want size savings, we can switch to eac3 with a bitrate.

If you want to preserve HDR10+ metadata when present

Add:

  --hdr-dynamic-metadata hdr10plus `


(Leave DV alone for now unless you explicitly want to experiment.)

Two CLI features that help your future queue worker
1) JSON progress/log output (super useful for a worker)

Use:

--json


Your worker can parse progress without scraping text.

2) Scan mode (discover tracks/languages before deciding)
C:\handbrakecli\HandBrakeCLI.exe -i "X:\path\to\movie.mkv" --scan


This is how your server/worker can decide:

“Has English audio?” if not → flag/skip or handle “foreign film” rule.

About --queue-import-file

That’s for importing a queue file created by the GUI, not really the clean API you want for your own system. For your custom pipeline: just call HandBrakeCLI directly per job.

If you paste the --scan output for one HDR movie (you can redact paths), I’ll tell you exactly what to key off of to implement:

keep English audio if exists, otherwise mark “needs review”

keep English subs only

detect DV vs HDR10/HDR10+ so you can skip DV if you want at first