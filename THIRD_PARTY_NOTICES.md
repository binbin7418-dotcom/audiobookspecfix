# Third-party notices

AudiobookSpecFix includes or ships alongside the following open-source components:

- **FFmpeg / FFprobe** — GPL-3.0 (essentials build 9.0.2 by Gyan,
  https://www.gyan.dev/ffmpeg/builds/). The Windows package places
  `ffmpeg.exe` and `ffprobe.exe` next to `AudiobookSpecFix.exe`; they are invoked
  as separate programs and are not modified. The full GPL-3.0 license text ships
  inside the package as `ffmpeg-LICENSE.txt`. Source code for this build is
  available from https://www.gyan.dev/ffmpeg/builds/ and https://ffmpeg.org
- **NumPy** — BSD 3-Clause License. https://numpy.org
- **LAME MP3 encoder** (invoked through FFmpeg) — LGPL. https://lame.sourceforge.io

The AudiobookSpecFix spec-checking, fix and re-check code, and the GUI, are our
own work and are covered by the licence in `LICENSE`.
