"""Operational helper hard-scoped to the authorized Railway pruebas service.

snapshot is read-only remotely. stage uploads only the maintenance package to
/tmp. apply requires --apply, uses one backed-up transaction and never swaps DBs.
No command accepts an alternate environment, project, service or database.
"""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile
import zlib

ROOT=Path(__file__).resolve().parents[2]
PROJECT='547dcb35-3521-486e-b706-55c936672bc3'
ENVIRONMENT='dc6a07af-8842-44c8-a072-a0d3e10ae203'
SERVICE='2279c8d8-0013-4794-a25b-cebf3e4fe9b1'
GUARD=f'''
import os
expected={{'RAILWAY_PROJECT_ID':{PROJECT!r},'RAILWAY_ENVIRONMENT_ID':{ENVIRONMENT!r},
 'RAILWAY_ENVIRONMENT_NAME':'pruebas','RAILWAY_SERVICE_ID':{SERVICE!r},
 'LESICO_DATABASE_PATH':'/data/lesico_astronomia.db'}}
if any(os.environ.get(key)!=value for key,value in expected.items()):
 raise RuntimeError('Identidad remota inesperada; operación rechazada')
'''


def railway_binary():
    if os.name=='nt':
        binary=Path(os.environ['APPDATA'])/'npm/node_modules/@railway/cli/bin/railway.exe'
        if binary.is_file():return str(binary)
    binary=shutil.which('railway')
    if not binary:raise RuntimeError('Railway CLI no disponible')
    return binary


def remote(code):
    encoded=base64.b64encode((GUARD+'\n'+code).encode('utf-8')).decode('ascii')
    command=f'''python -c "import base64;exec(base64.b64decode('{encoded}'))"'''
    result=subprocess.run([railway_binary(),'ssh','-p',PROJECT,'-e',ENVIRONMENT,'-s',SERVICE,'--',command],
                          capture_output=True,text=True,encoding='utf-8',timeout=120)
    if result.returncode:
        raise RuntimeError(result.stderr+'\n'+result.stdout)
    return result.stdout


def snapshot(destination):
    output=remote('''
import base64,sqlite3,zlib
from contextlib import closing
with closing(sqlite3.connect('file:/data/lesico_astronomia.db?mode=ro',uri=True)) as source:
 with closing(sqlite3.connect(':memory:')) as target:
  source.backup(target)
  if target.execute('PRAGMA integrity_check').fetchall()!=[('ok',)] or target.execute('PRAGMA foreign_key_check').fetchall():
   raise RuntimeError('Integridad SQLite falló')
  print('SNAPSHOT:'+base64.b64encode(zlib.compress(target.serialize())).decode('ascii'))
''')
    payload=next(line[len('SNAPSHOT:'):] for line in output.splitlines() if line.startswith('SNAPSHOT:'))
    data=zlib.decompress(base64.b64decode(payload))
    with Path(destination).open('xb') as stream:stream.write(data)
    return {'environment':'pruebas','snapshot':str(destination),'sha256':hashlib.sha256(data).hexdigest()}


def package():
    files=['usage_profile.py','migrations/023_alternative_usage_profile.py']
    files += ['migration/usage_profile_2026-09-18/'+name for name in (
        'safe_database.py','post_reconciliation.py','import_profiles.py','verify_candidate.py',
        'perfil_uso_contexto_consolidado.xlsx')]
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            info=zipfile.ZipInfo(name);info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,(ROOT/name).read_bytes())
    return buffer.getvalue()


def stage():
    data=package();digest=hashlib.sha256(data).hexdigest()
    folder='/tmp/lesico-usage-context-'+digest[:16]
    encoded=base64.b64encode(data).decode('ascii')
    chunks=[encoded[offset:offset+5000] for offset in range(0,len(encoded),5000)]
    for number,chunk in enumerate(chunks):
        remote(f'''
from pathlib import Path
folder=Path({folder!r});folder.mkdir(exist_ok=True)
part=folder/{str(number)+'.part'!r}
if part.exists() and part.read_text()!= {chunk!r}:raise RuntimeError('Parte incompatible')
part.write_text({chunk!r})
''')
    remote(f'''
import base64,hashlib,io,zipfile
from pathlib import Path
folder=Path({folder!r})
data=base64.b64decode(''.join((folder/(str(i)+'.part')).read_text() for i in range({len(chunks)})))
if hashlib.sha256(data).hexdigest()!= {digest!r}:raise RuntimeError('Paquete incompatible')
with zipfile.ZipFile(io.BytesIO(data)) as archive:
 for name in archive.namelist():
  target=(folder/name).resolve()
  if folder.resolve() not in target.parents:raise RuntimeError('Ruta de paquete inválida')
  target.parent.mkdir(parents=True,exist_ok=True)
  target.write_bytes(archive.read(name))
print('STAGED:'+str(folder))
''')
    return {'environment':'pruebas','folder':folder,'sha256':digest}


def apply_package():
    digest=hashlib.sha256(package()).hexdigest();folder='/tmp/lesico-usage-context-'+digest[:16]
    output=remote(f'''
import importlib.util,json,sys
from pathlib import Path
root=Path({folder!r})
sys.path.insert(0,str(root));sys.path.insert(0,str(root/'migration/usage_profile_2026-09-18'))
from safe_database import run
from post_reconciliation import corrections
from import_profiles import import_rows,read_sheet
from verify_candidate import verify
spec=importlib.util.spec_from_file_location('usage_migration',root/'migrations/023_alternative_usage_profile.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
rows=read_sheet(root/'migration/usage_profile_2026-09-18/perfil_uso_contexto_consolidado.xlsx','Perfil consolidado')
def operation(db):
 migration=module.migration(db)
 pending=corrections(db)
 profiles=import_rows(db,rows)
 if profiles['total_profiles']!=262:raise RuntimeError('Conteo de perfiles inesperado')
 return {{'changes':migration['changes']+pending['changes']+profiles['changes'],'migration':migration,'pending':pending,'profiles':profiles}}
report=run('/data/lesico_astronomia.db',operation,apply=True)
report['second_execution']=run('/data/lesico_astronomia.db',operation,apply=True)
report['acceptance']=verify('/data/lesico_astronomia.db',report['backup'])
from datetime import datetime,timezone
report_path=Path('/data/usage-context-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')
report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
report['report_path']=str(report_path)
print('REPORT:'+json.dumps(report,ensure_ascii=True))
''')
    return json.loads(next(line[len('REPORT:'):] for line in output.splitlines() if line.startswith('REPORT:')))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('snapshot','stage','apply'))
    parser.add_argument('--output',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    if args.action=='snapshot':result=snapshot(args.output)
    elif args.action=='stage':result=stage()
    else:
        if not args.apply:parser.error('apply requiere autorización explícita --apply')
        result=apply_package()
    if args.action!='snapshot':Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True,indent=2))
