# PyInstaller spec — build with: pyinstaller audiobookspecfix.spec
# ffmpeg.exe / ffprobe.exe are placed next to AudiobookSpecFix.exe by the CI workflow.
a = Analysis(['gui.py'], pathex=[], binaries=[], datas=[], hiddenimports=[],
             hookspath=[], runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='AudiobookSpecFix',
          console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='AudiobookSpecFix')
