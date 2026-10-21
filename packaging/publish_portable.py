"""Validate and publish a portable build from an immutable release tag."""
import argparse,hashlib,io,json,os,re,subprocess,sys,urllib.request,zipfile
from pathlib import Path

ROOT=Path.cwd()
REPO=os.environ['EXPECTED_REPO']
PRIVATE=os.environ['EXPECTED_PRIVATE']=='true'
TAG=os.environ['RELEASE_TAG']
SHA=os.environ['RELEASE_SHA']
PREFIX=os.environ['ARTIFACT_PREFIX']

def api(path):
    request=urllib.request.Request('https://api.github.com'+path,headers={
        'Authorization':'Bearer '+os.environ['GH_TOKEN'],
        'Accept':'application/vnd.github+json','User-Agent':'portable-release-validation'})
    with urllib.request.urlopen(request,timeout=60) as response:return json.load(response)

def command(*args):
    return subprocess.check_output(args,text=True,encoding='utf8').strip()

def verify_source():
    assert os.environ['GITHUB_REPOSITORY']==REPO
    assert re.fullmatch(r'v\d+\.\d+\.\d+',TAG)
    assert re.fullmatch(r'[0-9a-f]{40}',SHA)
    repo=api('/repos/'+REPO)
    assert repo['full_name']==REPO and repo['private'] is PRIVATE,'Repository boundary mismatch'
    assert command('git','rev-parse','HEAD')==SHA,'Unexpected source commit'
    assert api(f'/repos/{REPO}/git/ref/tags/{TAG}')['object']['sha']==SHA,'Tag changed'
    sys.path.insert(0,str(ROOT))
    from bk2.version import VERSION_TAG
    assert VERSION_TAG==TAG
    print(json.dumps({'repo':REPO,'private':PRIVATE,'tag':TAG,'source_sha':SHA}),flush=True)

def audit():
    stem=f'{PREFIX}-{TAG}-windows-x64'
    path=ROOT/'dist'/(stem+'.zip')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    checksum=path.with_suffix('.zip.sha256')
    assert checksum.read_text().split()[0]==digest
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        manifest=json.loads(archive.read(stem+'/build-manifest.json'))
        assert manifest['version']==TAG and not manifest['local_state_included']
        for name,expected in manifest['files'].items():
            assert hashlib.sha256(archive.read(stem+'/'+name)).hexdigest()==expected,name
        for name in archive.namelist():
            assert not any(part in name for part in ['/config/runtime/','/logs/','/evidence/','/tests/fixtures/'])
        with zipfile.ZipFile(io.BytesIO(archive.read(stem+'/Source.zip'))) as source:
            for name in source.namelist():
                expected=command('git','rev-parse',f'{SHA}:{name}')
                actual=subprocess.check_output(['git','hash-object','--stdin','--path='+name],input=source.read(name)).decode().strip()
                assert actual==expected,'Source differs from tag: '+name
    selftest=json.loads((ROOT/'dist'/stem/'logs/portable-self-test.json').read_text(encoding='utf8'))
    assert selftest['result']=='passed' and selftest['game_input'] is False
    evidence={'repo':REPO,'private':PRIVATE,'tag':TAG,'source_sha':SHA,'zip_sha256':digest,
              'size':path.stat().st_size,'manifest_files':len(manifest['files']),
              'source_verified':True,'selftest':selftest}
    (ROOT/'dist/release-validation.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf8')
    return path,checksum,evidence

def publish():
    verify_source()
    path,checksum,evidence=audit()
    releases=api('/repos/'+REPO+'/releases?per_page=100')
    matches=[r for r in releases if r['tag_name']==TAG]
    assert len(matches)<=1
    if matches:
        release=matches[0]
        assert release['draft'],'Refuse to alter published release'
        assert not release['assets'],'Refuse to overwrite existing assets'
    else:
        subprocess.run(['gh','release','create',TAG,'--repo',REPO,'--verify-tag','--draft',
            '--title',PREFIX+' '+TAG,'--notes-file',str(ROOT/f'RELEASE_{TAG}.md'),
            '--prerelease='+str(not PRIVATE).lower()],check=True)
    verify_source()
    subprocess.run(['gh','release','upload',TAG,str(path),str(checksum),'--repo',REPO],check=True)
    release=next(r for r in api('/repos/'+REPO+'/releases?per_page=100') if r['tag_name']==TAG)
    assert release['draft'] and len(release['assets'])==2
    for asset in release['assets']:
        local=ROOT/'dist'/asset['name']
        assert asset['state']=='uploaded' and asset['size']==local.stat().st_size
        assert asset['digest']=='sha256:'+hashlib.sha256(local.read_bytes()).hexdigest()
    verify_source()
    subprocess.run(['gh','release','edit',TAG,'--repo',REPO,'--draft=false',
        '--prerelease='+str(not PRIVATE).lower()],check=True)
    release=api(f'/repos/{REPO}/releases/tags/{TAG}')
    assert not release['draft'] and release['prerelease'] is (not PRIVATE)
    evidence['url']=release['html_url']
    evidence['assets']=[{k:a[k] for k in ['name','size','digest','browser_download_url']} for a in release['assets']]
    (ROOT/'dist/release-validation.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(evidence,ensure_ascii=False),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['verify-source','publish'])
    mode=parser.parse_args().mode
    verify_source() if mode=='verify-source' else publish()
