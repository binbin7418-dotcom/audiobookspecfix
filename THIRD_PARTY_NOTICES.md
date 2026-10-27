# Third-party notices

AudiobookSpecFix includes or ships alongside the following open-source components:

- **FFmpeg / FFprobe** — LGPL-2.1-or-later (static builds by BtbN,
  `ffmpeg-master-latest-win64-lgpl`). The Windows package places
  `ffmpeg.exe` and `ffprobe.exe` next to `AudiobookSpecFix.exe`; they are invoked
  as separate programs and are not modified. https://ffmpeg.org
- **NumPy** — BSD 3-Clause License. https://numpy.org
- **LAME MP3 encoder** (invoked through FFmpeg) — LGPL. https://lame.sourceforge.io

The AudiobookSpecFix spec-checking, fix and re-check code, and the GUI, are our
own work and are covered by the licence in `LICENSE`.
