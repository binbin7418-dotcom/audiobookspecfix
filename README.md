# AudiobookSpecFix — Audiobook ACX Spec Check, Fix & Re-Check

AudiobookSpecFix measures every chapter of an audiobook against the ACX
submission spec, fixes what a deterministic level/format pass can fix,
then **re-measures every output and reports fails first** — so you find
out before Audible does. 100% local — unpublished manuscript audio
never leaves your computer.

Built *for ACX submission*. ACX, Audible and Amazon are trademarks of
Amazon.com, Inc. or its affiliates; AudiobookSpecFix is an independent tool and
is not affiliated with or endorsed by them.

## The spec it checks (ACX submission requirements, checked 2026-10-26)

| Check | Requirement |
|---|---|
| Overall RMS | −23 to −18 dB |
| True peak | ≤ −3 dB |
| Noise floor (quietest 500 ms) | ≤ −60 dB RMS |
| Format | MP3, 192 kbps constant bitrate |
| Sample rate | 44.1 kHz |
| File length | ≤ 120 minutes per file |
| Head room tone | 0.5 to 1 second |
| Tail room tone | 1 to 5 seconds |
| Channels | One layout for the whole book (all mono or all stereo) |

## What it does
- **Measures everything above** on every chapter — RMS from a full PCM
  decode (not a metadata guess), true peak, the quietest 500 ms window
  as the noise floor, head/tail room tone from 50 ms frame analysis,
  and the MP3's real per-frame bitrate profile (VBR is detected from
  the frame headers, not the tag)
- **Fixes in one pass**: gain / limiting → channel unification to the
  book's layout → head/tail room tone trimmed or extended *with the
  chapter's own room tone*, carved from the middle of its longest quiet
  stretch → a single final encode to 192 kbps CBR MP3 at 44.1 kHz
- **Re-checks every output** and writes `acx_report.md`: fails first,
  then what was fixed, then per-file before/after for every check
- **Originals are never modified** — results go to a separate folder

## What it deliberately does not do
- **Long pauses (> 5 s) are flagged, never auto-cut.** A dramatic pause
  may be intentional; the report tells you where they are and you decide.
- **A noise floor recorded too hot is reported UNFIXABLE**, with the
  maths shown, and no output file is produced for that chapter.
  Level changes cannot remove hiss that was printed into the recording;
  anyone who "fixes" that silently is guessing. Re-record (or hire an
  engineer for real noise reduction).
- **Passing the machine spec is not a guarantee of passing ACX's human
  QC.** Pronunciation, performance, mouth clicks and extraneous sounds
  are not measured here.

## Verified behaviour
On a seeded set of 6 synthesised chapters — RMS −30.3 dB; a −0.8 dBTP
peak spike; a 48 kHz VBR encode with a 6.0 s interior pause; no head
tone with an 8.00 s tail; a stereo chapter in a mono book; and a noise
floor of −49.8 dB (designed unfixable) — the engine passed **102/102
acceptance checks**: chapters 1–5 were fixed and re-measured green on
every check (RMS to −20.8 / −22.3 dB, peak to −3.9 dBTP, head tone
0.00 → 0.75 s, tail 8.00 → 2.50 s, VBR/48 kHz re-encoded to 192 kbps
CBR / 44.1 kHz with the 6 s pause flagged and *kept*, stereo downmixed
to the book layout), chapter 6 was reported UNFIXABLE with no output
file, no check that was green before the fix went red after it (new
defects = 0), and the originals' SHA-256 hashes were unchanged.
See TEST-RESULTS.txt in the release notes.

## Run from source
```
pip install -r requirements.txt   # numpy; ffmpeg/ffprobe must be on PATH
python acx_ready.py my_book_chapters my_book_fixed
python acx_ready.py my_book_chapters my_book_report --check   # measure only
```
Writes fixed chapters plus `acx_report.md` into the output folder.
The Windows app (`gui.py`, packaged with PyInstaller) wraps the same
engine and ships with ffmpeg alongside.

## Scope
One folder = one book; chapters are processed in filename order and the
book's channel layout follows its first chapter. Chapter files up to
120 minutes each, per the spec.
