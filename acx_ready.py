#!/usr/bin/env python3
"""
AudiobookSpecFix — audiobook chapter check, fix & re-check against the ACX spec.

What it does (via the bundled ffmpeg/ffprobe, everything local):
  1. Measures every chapter MP3 in a folder: overall RMS level, true peak,
     noise floor (quietest 500 ms window), head/tail room tone, long pauses,
     channel layout, and the MP3's codec / sample rate / bitrate / CBR-ness.
  2. Judges each measurement against the published ACX submission spec
     (SPEC below, versioned and dated — the numbers are not hard-coded
     folklore, they are the configuration this build checks against).
  3. Fixes what a deterministic chain can fix: gain / limiting -> channel
     unification -> head/tail room-tone trim or extension -> ONE final
     encode to 192 kbps CBR MP3 at 44.1 kHz.
  4. Re-measures every output and writes a fails-first before/after report.

What it never does:
  - Long pauses (> 5 s) are flagged in the report, never auto-cut.
  - A noise floor recorded too hot is NOT denoised and NOT hidden: if the
    level maths cannot bring RMS and floor into spec at the same time, the
    chapter is reported UNFIXABLE (re-record) and no "fixed" file is made.
  - Passing these machine checks does not guarantee passing ACX's human
    QC (pronunciation, performance, extraneous sounds are out of scope).

Originals are never modified; fixed files go to a separate output folder.
"""
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np

# ---------------------------------------------------------------------------
# The spec this build checks against. Versioned + dated on purpose: if ACX
# changes its published requirements, this block (and only this block) moves.
# Source: ACX audio submission requirements as published and summarised at
# https://www.acx.com/help/acx-audio-submission-requirements/200485520
# (checked 2026-10-26).
# ---------------------------------------------------------------------------
SPEC = {
    "version": "ACX submission requirements, checked 2026-10-26",
    "rms_min": -23.0,          # overall RMS, dBFS
    "rms_max": -18.0,
    "peak_max": -3.0,          # true peak ceiling, dBTP
    "floor_max": -60.0,        # noise floor (quietest 500 ms RMS), dBFS
    "bitrate_kbps": 192,       # MP3, constant bitrate
    "sample_rate": 44100,
    "max_duration_s": 120 * 60,
    "head_min": 0.5,           # head room tone, seconds
    "head_max": 1.0,
    "tail_min": 1.0,           # tail room tone, seconds
    "tail_max": 5.0,
    "pause_flag_s": 5.0,       # pauses longer than this are flagged, not cut
}

# Working targets for the fix chain (inside the spec, with encode headroom).
TARGET_RMS = -20.5             # middle of the -23..-18 window
PEAK_WORK_CEIL = -3.6          # aim peaks here so MP3 encode overshoot stays <= -3.0
FLOOR_WORK = -60.8             # aim the floor here so re-measurement stays <= -60
HEAD_TARGET = 0.75             # room-tone targets when head/tail must be rebuilt
TAIL_TARGET = 2.5
FRAME_S = 0.05                 # analysis frame: 50 ms
WIN_FRAMES = 10                # noise-floor window: 10 frames = 500 ms
EPS = 1e-12


def find_tool(name):
    here = os.path.dirname(os.path.abspath(sys.argv[0]))
    exe_dir = os.path.dirname(sys.executable)
    for d in (here, exe_dir, os.getcwd()):
        p = os.path.join(d, name + (".exe" if os.name == "nt" else ""))
        if os.path.isfile(p):
            return p
    return shutil.which(name)


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=7200, **kw)


def db(x):
    """Linear power ratio -> dB, with silence clamped."""
    return 10.0 * math.log10(x) if x > EPS else -120.0


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------
def probe(path):
    fp = find_tool("ffprobe")
    if not fp:
        raise RuntimeError("ffprobe not found next to the app or on PATH")
    r = _run([fp, "-v", "quiet", "-show_entries",
              "stream=codec_name,sample_rate,channels,bit_rate:format=duration,bit_rate",
              "-of", "json", path])
    d = json.loads(r.stdout)
    st = (d.get("streams") or [{}])[0]
    fmt = d.get("format") or {}
    br = st.get("bit_rate") or fmt.get("bit_rate")
    return {
        "codec": st.get("codec_name"),
        "sample_rate": int(st["sample_rate"]) if st.get("sample_rate") else None,
        "channels": int(st["channels"]) if st.get("channels") else None,
        "bit_rate": int(br) if br else None,
        "duration": float(fmt["duration"]) if fmt.get("duration") else None,
    }


def pcm_analysis(path, channels):
    """Chunked decode; returns dict with frame_rms_db (list), overall rms_db,
    sample_peak_db, floor_db (quietest 500 ms), quietest_win_start_s."""
    ff = find_tool("ffmpeg")
    if not ff:
        raise RuntimeError("ffmpeg not found next to the app or on PATH")
    sr = SPEC["sample_rate"]
    frame_len = int(sr * FRAME_S)
    cmd = [ff, "-v", "error", "-i", path, "-ar", str(sr),
           "-f", "f32le", "-acodec", "pcm_f32le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    pending = np.empty((0, channels), dtype=np.float64)
    frame_pow = []
    total_pow = 0.0
    total_n = 0
    peak = 0.0
    read_instants = frame_len * 100
    while True:
        raw = proc.stdout.read(read_instants * channels * 4)
        if not raw:
            break
        a = np.frombuffer(raw, dtype="<f4").astype(np.float64)
        if a.size % channels:
            a = a[: a.size - (a.size % channels)]
        a = a.reshape(-1, channels)
        pending = np.vstack([pending, a]) if pending.size else a
        peak = max(peak, float(np.max(np.abs(a))) if a.size else 0.0)
        while pending.shape[0] >= frame_len:
            fr, pending = pending[:frame_len], pending[frame_len:]
            p = float(np.mean(fr * fr))
            frame_pow.append(p)
            total_pow += p * frame_len
            total_n += frame_len
    proc.wait()
    if pending.shape[0] > 0:  # final partial frame counts toward overall only
        p = float(np.mean(pending * pending))
        total_pow += p * pending.shape[0]
        total_n += pending.shape[0]
    if total_n == 0:
        raise RuntimeError(f"could not decode audio from {path}")
    frame_rms_db = [db(p) for p in frame_pow]
    # quietest 500 ms window (sliding, step 1 frame)
    floor_db, floor_start = -120.0, 0.0
    if len(frame_pow) >= WIN_FRAMES:
        arr = np.array(frame_pow)
        win = np.convolve(arr, np.ones(WIN_FRAMES), mode="valid") / WIN_FRAMES
        idx = int(np.argmin(win))
        floor_db, floor_start = db(float(win[idx])), idx * FRAME_S
    elif frame_pow:
        floor_db = db(float(np.mean(frame_pow)))
    return {
        "frame_rms_db": frame_rms_db,
        "rms_db": db(total_pow / total_n),
        "sample_peak_db": db(peak * peak),
        "floor_db": floor_db,
        "quietest_start_s": floor_start,
        "n_frames": len(frame_pow),
    }


def true_peak_db(path):
    """True peak via ffmpeg loudnorm analysis (same approach as AudioFix)."""
    ff = find_tool("ffmpeg")
    r = _run([ff, "-hide_banner", "-i", path, "-af",
              "loudnorm=print_format=json", "-f", "null", "-"])
    m = re.search(r"\{\s*\"input_i\".*?\}", r.stderr, re.S)
    if not m:
        raise RuntimeError("loudnorm analysis failed — invalid audio file?")
    return float(json.loads(m.group(0))["input_tp"])


# --- MP3 constant-bitrate verification: parse frame headers directly -------
_BITRATES_V1_L3 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
_BITRATES_V2_L3 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
_SAMPLE_RATES = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def mp3_bitrate_profile(path, max_frames=4000):
    """Walk MP3 frame headers. Returns (is_cbr, kbps_set, frames_parsed).
    is_cbr is None when too few frames could be parsed to judge."""
    with open(path, "rb") as fh:
        data = fh.read(8 * 1024 * 1024)
    pos = 0
    if data[:3] == b"ID3" and len(data) > 10:
        size = ((data[6] & 0x7F) << 21) | ((data[7] & 0x7F) << 14) | \
               ((data[8] & 0x7F) << 7) | (data[9] & 0x7F)
        pos = 10 + size
    kbps_seen = []
    n = len(data)
    while pos + 4 <= n and len(kbps_seen) < max_frames:
        if data[pos] != 0xFF or (data[pos + 1] & 0xE0) != 0xE0:
            nxt = data.find(b"\xff", pos + 1)
            if nxt < 0:
                break
            pos = nxt
            continue
        ver_bits = (data[pos + 1] >> 3) & 0x3
        layer_bits = (data[pos + 1] >> 1) & 0x3
        if ver_bits == 1 or layer_bits != 1:  # reserved version / not Layer III
            pos += 1
            continue
        br_idx = (data[pos + 2] >> 4) & 0xF
        sr_idx = (data[pos + 2] >> 2) & 0x3
        pad = (data[pos + 2] >> 1) & 0x1
        if br_idx in (0, 15) or sr_idx == 3:
            pos += 1
            continue
        kbps = (_BITRATES_V1_L3 if ver_bits == 3 else _BITRATES_V2_L3)[br_idx]
        sr = _SAMPLE_RATES[ver_bits][sr_idx]
        frame_len = int((144 if ver_bits == 3 else 72) * kbps * 1000 / sr) + pad
        if frame_len < 24:
            pos += 1
            continue
        kbps_seen.append(kbps)
        pos += frame_len
    if len(kbps_seen) < 50:
        return None, set(kbps_seen), len(kbps_seen)
    return len(set(kbps_seen)) == 1, set(kbps_seen), len(kbps_seen)


def _quiet_runs(frame_rms_db, floor_db):
    """Quiet = frame RMS at/below min(-50 dBFS, floor + 18 dB). Returns
    (head_s, tail_s, interior_pauses[(start_s, dur_s)],
     all_runs[(start_s, dur_s)] for runs >= 0.3 s, edges included)."""
    thr = min(-50.0, floor_db + 18.0)
    n = len(frame_rms_db)
    quiet = [v <= thr for v in frame_rms_db]
    head = 0
    while head < n and quiet[head]:
        head += 1
    tail = 0
    while tail < n - head and quiet[n - 1 - tail]:
        tail += 1
    runs = []
    i = 0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if (j - i) * FRAME_S >= 0.3:
                runs.append((i * FRAME_S, (j - i) * FRAME_S))
            i = j
        else:
            i += 1
    pauses = [(s, d) for (s, d) in runs
              if s >= head * FRAME_S - 1e-9
              and s + d <= (n - tail) * FRAME_S + 1e-9
              and d > SPEC["pause_flag_s"]
              and s > 0 and s + d < n * FRAME_S]
    return head * FRAME_S, tail * FRAME_S, pauses, runs


def measure(path):
    """Full measurement of one chapter file."""
    pr = probe(path)
    ch = pr["channels"] or 1
    an = pcm_analysis(path, ch)
    tp = true_peak_db(path)
    head, tail, pauses, runs = _quiet_runs(an["frame_rms_db"], an["floor_db"])
    cbr, kbps_set, _ = (mp3_bitrate_profile(path) if pr["codec"] == "mp3"
                        else (None, set(), 0))
    return {
        "path": path, "name": os.path.basename(path),
        "codec": pr["codec"], "sample_rate": pr["sample_rate"],
        "channels": ch, "bit_rate": pr["bit_rate"], "duration": pr["duration"],
        "rms_db": an["rms_db"], "peak_db": tp,
        "sample_peak_db": an["sample_peak_db"],
        "floor_db": an["floor_db"], "quietest_start_s": an["quietest_start_s"],
        "head_s": head, "tail_s": tail, "pauses": pauses, "quiet_runs": runs,
        "is_cbr": cbr, "kbps_set": kbps_set,
    }


# ---------------------------------------------------------------------------
# Judgement
# ---------------------------------------------------------------------------
def check_file(m, book_channels=None):
    """Returns a list of check dicts: key/label/measured/required/status.
    Status is PASS or FAIL; long pauses are separate annotations, not checks."""
    S = SPEC
    out = []

    def add(key, label, measured, required, ok):
        out.append({"key": key, "label": label, "measured": measured,
                    "required": required, "status": "PASS" if ok else "FAIL"})

    add("rms", "Overall RMS", f"{m['rms_db']:.1f} dB",
        f"{S['rms_min']:.0f} to {S['rms_max']:.0f} dB",
        S["rms_min"] <= m["rms_db"] <= S["rms_max"])
    add("peak", "True peak", f"{m['peak_db']:.1f} dBTP",
        f"<= {S['peak_max']:.0f} dB", m["peak_db"] <= S["peak_max"])
    add("floor", "Noise floor (quietest 500 ms)", f"{m['floor_db']:.1f} dB RMS",
        f"<= {S['floor_max']:.0f} dB RMS", m["floor_db"] <= S["floor_max"])
    fmt_ok = (m["codec"] == "mp3" and m["sample_rate"] == S["sample_rate"]
              and m["kbps_set"] == {S["bitrate_kbps"]} and m["is_cbr"] is True)
    kbps_txt = ("/".join(str(k) for k in sorted(m["kbps_set"])) + " kbps"
                if m["kbps_set"] else "unknown bitrate")
    add("format", "File format",
        f"{m['codec']}, {m['sample_rate']} Hz, {kbps_txt}"
        + (", CBR" if m["is_cbr"] else ", VBR/unverified"),
        f"mp3, {S['sample_rate']} Hz, {S['bitrate_kbps']} kbps CBR", fmt_ok)
    add("duration", "Duration", f"{m['duration']:.0f} s" if m["duration"] else "?",
        f"<= {S['max_duration_s'] // 60} min",
        bool(m["duration"]) and m["duration"] <= S["max_duration_s"])
    add("head", "Head room tone", f"{m['head_s']:.2f} s",
        f"{S['head_min']} to {S['head_max']} s",
        S["head_min"] <= m["head_s"] <= S["head_max"])
    add("tail", "Tail room tone", f"{m['tail_s']:.2f} s",
        f"{S['tail_min']} to {S['tail_max']} s",
        S["tail_min"] <= m["tail_s"] <= S["tail_max"])
    if book_channels is not None:
        add("channels", "Channels (book-uniform)", f"{m['channels']} ch",
            f"{book_channels} ch for the whole book",
            m["channels"] == book_channels and m["channels"] in (1, 2))
    return out


def fails(checks):
    return [c for c in checks if c["status"] == "FAIL"]


# ---------------------------------------------------------------------------
# Fix chain
# ---------------------------------------------------------------------------
def plan_gain(m):
    """Decide the gain/limiter plan, or return unfixable reasons.
    Returns (plan, reasons): plan = {'gain': dB, 'limiter': bool}."""
    reasons = []
    gain_rms = TARGET_RMS - m["rms_db"]
    gain_floor_cap = FLOOR_WORK - m["floor_db"]     # gain beyond this breaks the floor
    gain = min(gain_rms, gain_floor_cap)
    if m["rms_db"] + gain < SPEC["rms_min"]:
        reasons.append(
            f"noise floor {m['floor_db']:.1f} dB is too hot: reaching the RMS "
            f"window would push the floor above {SPEC['floor_max']:.0f} dB, and "
            "attenuating enough to fix the floor drags RMS below "
            f"{SPEC['rms_min']:.0f} dB. This was recorded, not mastered, wrong "
            "— it needs re-recording (or real noise reduction by an engineer), "
            "not a level fix.")
        return None, reasons
    gain_peak_cap = PEAK_WORK_CEIL - m["peak_db"]
    limiter = False
    if gain > gain_peak_cap:
        if m["rms_db"] + gain_peak_cap >= SPEC["rms_min"]:
            gain = gain_peak_cap            # plain attenuation reaches both
        else:
            limiter = True                  # gain for RMS, limiter holds peaks
    return {"gain": round(gain, 2), "limiter": limiter}, reasons


def _extract_tone(ff, src_wav, measured, seconds, dst):
    """Carve room tone from the CENTRE of the file's longest quiet run
    (the middle of a long quiet stretch cannot contain a voice edge),
    looped if the piece needed is longer than the patch carved."""
    chunk = dst + ".chunk.wav"
    runs = measured.get("quiet_runs") or []
    if runs:
        s, d = max(runs, key=lambda r: r[1])
        start = max(0.0, s + d / 2.0 - 0.3)
    else:
        start = max(0.0, measured["quietest_start_s"] - 0.05)
    r = _run([ff, "-hide_banner", "-y", "-ss", f"{start:.2f}", "-t", "0.60",
              "-i", src_wav, chunk])
    if r.returncode != 0 or not os.path.isfile(chunk):
        return False
    r = _run([ff, "-hide_banner", "-y", "-stream_loop", "-1", "-i", chunk,
              "-t", f"{seconds:.2f}", dst])
    ok = r.returncode == 0 and os.path.isfile(dst)
    if os.path.isfile(chunk):
        os.remove(chunk)
    return ok


def _synth_tone(ff, floor_db, channels, seconds, dst):
    """Fallback room tone: brown noise at the measured floor level."""
    amp = min(0.02, max(0.0005, 10 ** (floor_db / 20.0)))
    r = _run([ff, "-hide_banner", "-y", "-f", "lavfi", "-i",
              f"anoisesrc=color=brown:amplitude={amp:.5f}", "-t",
              f"{seconds:.2f}", "-ar", str(SPEC["sample_rate"]),
              "-ac", str(channels), dst])
    return r.returncode == 0 and os.path.isfile(dst)


def fix_file(src, dst, target_channels, workdir):
    """Run the fix chain for one chapter. Returns a result dict.
    Chain: gain/limiter -> channel unify (one processing pass to WAV)
           -> head/tail room-tone trim/extend -> ONE final MP3 encode."""
    ff = find_tool("ffmpeg")
    m0 = measure(src)
    plan, reasons = plan_gain(m0)
    if plan is None:
        return {"name": m0["name"], "status": "UNFIXABLE", "before": m0,
                "reasons": reasons, "output": None}
    os.makedirs(workdir, exist_ok=True)
    base = os.path.splitext(m0["name"])[0]
    processed = os.path.join(workdir, base + "_proc.wav")
    filters = []
    if abs(plan["gain"]) >= 0.05:
        filters.append(f"volume={plan['gain']:.2f}dB")
    if plan["limiter"]:
        lim = 10 ** (PEAK_WORK_CEIL / 20.0)
        filters.append(f"alimiter=limit={lim:.4f}:attack=5:release=50:level=false")
    cmd = [ff, "-hide_banner", "-y", "-i", src]
    if filters:
        cmd += ["-af", ",".join(filters)]
    cmd += ["-ar", str(SPEC["sample_rate"]), "-ac", str(target_channels), processed]
    r = _run(cmd)
    if r.returncode != 0:
        raise RuntimeError("processing pass failed: " +
                             (r.stderr.strip().splitlines()[-1] if r.stderr else "?"))
    # Re-measure the processed WAV to plan room tone on real numbers.
    m1 = measure(processed)
    trim_head = trim_tail = 0.0
    add_head = add_tail = 0.0
    if m1["head_s"] > SPEC["head_max"]:
        trim_head = m1["head_s"] - HEAD_TARGET
    elif m1["head_s"] < SPEC["head_min"]:
        add_head = HEAD_TARGET - m1["head_s"]
    if m1["tail_s"] > SPEC["tail_max"]:
        trim_tail = m1["tail_s"] - TAIL_TARGET
    elif m1["tail_s"] < SPEC["tail_min"]:
        add_tail = TAIL_TARGET - m1["tail_s"]

    pieces = []
    tmp = tempfile.mkdtemp(prefix="acxrt_", dir=workdir)
    try:
        if add_head > 0.02:
            tone = os.path.join(tmp, "tone_head.wav")
            if not _extract_tone(ff, processed, m1, add_head, tone):
                _synth_tone(ff, m1["floor_db"], target_channels, add_head, tone)
            pieces.append(tone)
        main = os.path.join(tmp, "main.wav")
        dur = (m1["duration"] or 0) - trim_head - trim_tail
        r = _run([ff, "-hide_banner", "-y", "-ss", f"{trim_head:.2f}", "-t",
                  f"{dur:.2f}", "-i", processed, main])
        if r.returncode != 0:
            raise RuntimeError("room-tone trim failed")
        pieces.append(main)
        if add_tail > 0.02:
            tone = os.path.join(tmp, "tone_tail.wav")
            if not _extract_tone(ff, processed, m1, add_tail, tone):
                _synth_tone(ff, m1["floor_db"], target_channels, add_tail, tone)
            pieces.append(tone)
        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w", encoding="utf-8") as fh:
            for p in pieces:
                fh.write(f"file '{os.path.abspath(p)}'\n")
        r = _run([ff, "-hide_banner", "-y", "-f", "concat", "-safe", "0",
                  "-i", lst, "-codec:a", "libmp3lame", "-b:a",
                  f"{SPEC['bitrate_kbps']}k", "-ar", str(SPEC["sample_rate"]),
                  "-ac", str(target_channels), dst])
        if r.returncode != 0:
            raise RuntimeError("final encode failed: " +
                                 (r.stderr.strip().splitlines()[-1] if r.stderr else "?"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    m2 = measure(dst)
    return {"name": m0["name"], "status": "FIXED", "before": m0, "after": m2,
            "plan": plan, "output": dst,
            "notes": [f"gain {plan['gain']:+.1f} dB"
                      + (" + limiter" if plan["limiter"] else ""),
                      f"channels -> {target_channels}",
                      f"head trim {trim_head:.2f} s / add {add_head:.2f} s, "
                      f"tail trim {trim_tail:.2f} s / add {add_tail:.2f} s"]}


# ---------------------------------------------------------------------------
# Book run + fails-first report
# ---------------------------------------------------------------------------
def run_book(indir, outdir, fix=True):
    files = sorted(f for f in os.listdir(indir)
                   if f.lower().endswith((".mp3", ".wav", ".m4a", ".flac")))
    if not files:
        raise RuntimeError(f"no audio files found in {indir}")
    os.makedirs(outdir, exist_ok=True)
    paths = [os.path.join(indir, f) for f in files]
    measures = [measure(p) for p in paths]
    book_ch = measures[0]["channels"]  # the book follows its first chapter
    workdir = os.path.join(outdir, "_work")
    results = []
    for m, p in zip(measures, paths):
        pre = check_file(m, book_ch)
        entry = {"name": m["name"], "before": m, "pre_checks": pre,
                 "pauses": m["pauses"]}
        if not fix:
            entry["status"] = "CHECKED"
        elif not fails(pre) and m["channels"] == book_ch:
            dst = os.path.join(outdir, m["name"])
            if os.path.abspath(p) != os.path.abspath(dst):
                shutil.copy2(p, dst)
            entry.update(status="ALREADY PASS", output=dst, after=m,
                         post_checks=pre, notes=["already compliant — copied unchanged"])
        else:
            res = fix_file(p, os.path.join(outdir, m["name"]), book_ch, workdir)
            entry.update(status=res["status"], output=res.get("output"),
                         after=res.get("after"), notes=res.get("notes", []),
                         reasons=res.get("reasons", []))
            if res.get("after"):
                entry["post_checks"] = check_file(res["after"], book_ch)
        results.append(entry)
    if os.path.isdir(workdir):
        shutil.rmtree(workdir, ignore_errors=True)
    report = build_report(results, book_ch, fix)
    report_path = os.path.join(outdir, "acx_report.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(report)
    book_ok = all(_entry_ok(e) for e in results)
    return {"results": results, "report_path": report_path,
            "book_verdict": "READY" if book_ok else "NOT READY",
            "book_channels": book_ch}


def _entry_ok(e):
    """An entry counts toward a READY book only if the checks that stand
    for it (post-fix when a fix ran, otherwise the check-only results)
    are all green."""
    if e["status"] == "UNFIXABLE":
        return False
    return not fails(e.get("post_checks") or e["pre_checks"])


def _fmt_checks(checks, before_checks=None):
    lines = []
    bmap = {c["key"]: c for c in (before_checks or [])}
    for c in checks:
        b = bmap.get(c["key"])
        bt = f" (was {b['measured']})" if b and b["measured"] != c["measured"] else ""
        lines.append(f"  - [{c['status']}] {c['label']}: {c['measured']} "
                     f"(required {c['required']}){bt}")
    return lines


def build_report(results, book_ch, fix):
    L = ["# AudiobookSpecFix — ACX spec check, fix & re-check report", "",
         f"Spec: {SPEC['version']} · Book channel layout: "
         f"{'mono' if book_ch == 1 else 'stereo'} ({book_ch} ch, follows the "
         "first chapter; every chapter must match)", ""]
    still, unfixable, fixed_ok = [], [], []
    for e in results:
        if e["status"] == "UNFIXABLE":
            unfixable.append(e)
        elif fails(e.get("post_checks") or e["pre_checks"]):
            still.append(e)
        elif e["status"] in ("FIXED", "ALREADY PASS"):
            fixed_ok.append(e)
    L += ["## FAILS FIRST — " + ("still failing after the fix pass" if fix
                                  else "failing chapters (check-only run, nothing was fixed)")]
    if still or unfixable:
        for e in unfixable:
            bad = fails(e["pre_checks"])
            L.append(f"- {e['name']} — UNFIXABLE, no output file made:")
            for c in bad:
                L.append(f"  - [FAIL] {c['label']}: {c['measured']} "
                         f"(required {c['required']})")
            for r in e.get("reasons", []):
                L.append(f"  Why: {r}")
        for e in still:
            L.append(f"- {e['name']} — still failing:")
            for c in fails(e.get("post_checks") or e["pre_checks"]):
                b = {x["key"]: x for x in e["pre_checks"]}.get(c["key"])
                bt = (f" (was {b['measured']})"
                      if b and b["measured"] != c["measured"] else "")
                L.append(f"  - [FAIL] {c['label']}: {c['measured']} "
                         f"(required {c['required']}){bt}")
    else:
        L.append("- None. Every chapter passes every check"
                 + (" on re-measurement." if fix else "."))
    L += ["", "## Fixed and re-verified"]
    if fixed_ok:
        for e in fixed_ok:
            tag = "already compliant" if e["status"] == "ALREADY PASS" else "fixed"
            L.append(f"- {e['name']} — {tag}, all checks PASS after re-measurement")
    else:
        L.append("- None.")
    L += ["", "## Per-file detail"]
    for e in results:
        L += ["", f"### {e['name']} — {e['status']}"]
        checks = e.get("post_checks") or e["pre_checks"]
        L += _fmt_checks(checks, e["pre_checks"] if e.get("post_checks") else None)
        for n in e.get("notes", []):
            L.append(f"  - fix: {n}")
        if e["pauses"]:
            L.append(f"  - long pauses > {SPEC['pause_flag_s']:.0f} s "
                     "(flagged only, never auto-cut):")
            for s, d in e["pauses"]:
                L.append(f"    - at {s:.1f} s, lasting {d:.1f} s")
    book_ok = all(_entry_ok(e) for e in results)
    L += ["", "## Book verdict",
          f"- {'READY' if book_ok else 'NOT READY'}: "
          + ("every chapter passes every machine check in the spec above."
             if book_ok else
             "at least one chapter still fails — see FAILS FIRST. "
             "Scope note: these are the machine-checkable ACX requirements. "
             "Passing them does not guarantee passing ACX's human QC "
             "(pronunciation, performance and extraneous sounds are not "
             "measured here).")]
    return "\n".join(L) + "\n"


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    check_only = "--check" in argv
    if len(args) < 2:
        print("usage: acx_ready.py <chapter folder> <output folder> [--check]")
        return 2
    s = run_book(args[0], args[1], fix=not check_only)
    print(f"book verdict: {s['book_verdict']} · report: {s['report_path']}")
    for e in s["results"]:
        print(f"  {e['name']}: {e['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
