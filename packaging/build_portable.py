"""Build from allowlisted tracked files; never distribute local plans or state.

Run with the project's Python after installing requirements-build.txt.
Output locations must be new; existing distributions are never overwritten.
"""
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bk2.version import VERSION_TAG


def tracked_files():
    raw = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT)
    return [Path(x.decode('utf-8')) for x in raw.split(b'\0') if x]


def distributable(path):
    # Explicitly omit all user state, evidence and full screenshots, even if staged.
    return (path.parts[0] not in {'.venv', 'logs', 'evidence', 'backups', 'research', 'dist', 'build'}
            and path.parts[:2] not in {('config', 'runtime'), ('tests', 'fixtures')}
            and not any(p in {'.git', '__pycache__'} for p in path.parts)
            and path.suffix.lower() not in {'.log', '.pyc', '.dmp', '.dump', '.ct'}
            and not path.name.startswith('.env'))


def source_bytes(path):
    if path.as_posix() == 'config/dungeon_plan.json':
        return subprocess.check_output(['git', 'show', 'HEAD:config/dungeon_plan.json'], cwd=ROOT)
    return (ROOT / path).read_bytes()


def main():
    output = ROOT / 'dist' / f'BlackfireKeeper-{VERSION_TAG}-windows-x64'
    if output.exists():
        raise SystemExit(f'Output already exists; keep it or move it before rebuilding: {output}')
    staging = ROOT / 'build' / f'portable-{VERSION_TAG}'
    if staging.exists():
        raise SystemExit(f'Build staging already exists: {staging}')
    staging.mkdir(parents=True)
    files = [p for p in tracked_files() if distributable(p)]
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm',
        '--distpath', str(staging / 'dist'), '--workpath', str(staging / 'work'),
        str(ROOT / 'packaging/BlackfireKeeper.spec')], cwd=ROOT, check=True)
    # Assemble separate from the frozen library folder; root stays writable and movable.
    shutil.copytree(staging / 'dist/BlackfireKeeper', output)
    for path in files:
        if path.parts[0] in {'assets', 'config', 'third_party', 'docs'} or path.name in {
                'LICENSE', 'README.md', 'THIRD_PARTY_NOTICES.md', 'requirements.txt',
                '使用说明.html', '使用说明.md'} or path.name.startswith('RELEASE_v'):
            target = output / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source_bytes(path))
    # Exact corresponding source, including build recipe and the public default plan.
    with zipfile.ZipFile(output / 'Source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.writestr(path.as_posix(), source_bytes(path))
    licenses = output / 'third_party/bundled'
    licenses.mkdir(parents=True, exist_ok=True)
    dependencies = {}
    for name in ('MaaFw', 'MaaAgentBinary', 'numpy', 'Pillow', 'StrEnum', 'PyInstaller'):
        dist = importlib.metadata.distribution(name)
        dependencies[name] = dist.version
        for path in dist.files or []:
            if any(word in str(path).lower() for word in ('license', 'copying', 'notice')):
                src = Path(dist.locate_file(path))
                if src.is_file():
                    dst = licenses / name / str(path)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, dst)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if not python_license.is_file():
        raise RuntimeError('Python license missing')
    shutil.copyfile(python_license, licenses / 'Python-LICENSE.txt')
    for path in (Path(sys.base_prefix) / 'tcl').rglob('license*'):
        if path.is_file():
            dst = licenses / 'TclTk' / path.relative_to(Path(sys.base_prefix) / 'tcl')
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dst)
    (output / '重新选择游戏.cmd').write_text('@echo off\r\nstart "" "%~dp0BlackfireKeeper.exe" --select-game\r\n', encoding='ascii')
    (output / '离线自检.cmd').write_text('@echo off\r\n"%~dp0BlackfireKeeper-cli.exe" --self-test\r\npause\r\n', encoding='ascii')
    manifest = dict(version=VERSION_TAG, python=sys.version, dependencies=dependencies,
        local_state_included=False, files={p.relative_to(output).as_posix():
            hashlib.file_digest(p.open('rb'), 'sha256').hexdigest()
            for p in sorted(output.rglob('*')) if p.is_file()})
    (output / 'build-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    zip_path = shutil.make_archive(str(output), 'zip', output.parent, output.name)
    digest = hashlib.file_digest(open(zip_path, 'rb'), 'sha256').hexdigest()
    Path(zip_path + '.sha256').write_text(f'{digest}  {Path(zip_path).name}\n', encoding='ascii')
    print(json.dumps(dict(folder=str(output), zip=zip_path, sha256=digest), ensure_ascii=False))


if __name__ == '__main__':
    main()
