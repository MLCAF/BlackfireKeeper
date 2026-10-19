# Build two entry points sharing one folder of Python/native dependencies.
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).parent
maa_data, maa_bins, maa_hidden = collect_all('maa')
a = Analysis([str(root / 'packaging/entry.py')], pathex=[str(root)],
             binaries=maa_bins, datas=maa_data, hiddenimports=maa_hidden,
             hookspath=[], runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='BlackfireKeeper',
          debug=False, strip=False, upx=False, console=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name='BlackfireKeeper-cli',
          debug=False, strip=False, upx=False, console=True)
coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False,
               name='BlackfireKeeper')
