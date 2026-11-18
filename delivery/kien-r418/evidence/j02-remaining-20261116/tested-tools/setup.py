import json,secrets,socket,subprocess,sys
from pathlib import Path
import httpx
sys.stdout.reconfigure(encoding='utf-8')
folder=Path(__file__).parent
private=folder/'private.json'
if private.exists():
    print('Existing owned J02 fixture retained');sys.exit(0)
material=json.loads(Path('C:/t/aletheia-k14-staging-da2093353a73/bootstrap.json').read_text())
dns=socket.getaddrinfo
socket.getaddrinfo=lambda host,*args,**kwargs:dns('127.0.0.1' if host=='idp-da2093353a73.test' else host,*args,**kwargs)
base='https://idp-da2093353a73.test:18877'
issuer='https://idp-da2093353a73.test:18777/realms/k14'
schema='k17_seed_97e97d480c774754bfa46bbc8ff61369'
run=secrets.token_hex(4)
password=secrets.token_urlsafe(24)
actors=[]
with httpx.Client(verify='C:/t/aletheia-k14-staging-da2093353a73/ca.pem',trust_env=False,timeout=20) as client:
    r=client.post(base+'/realms/master/protocol/openid-connect/token',data={'grant_type':'password','client_id':'admin-cli','username':'k14-admin','password':material['idp_password']})
    assert r.status_code==200,('admin authentication',r.status_code)
    headers={'Authorization':'Bearer '+r.json()['access_token']}
    for index in range(7):
        username=f'j02_{run}_{index}'
        email=username+'@aletheia.invalid'
        value={'username':username,'enabled':True,'emailVerified':True,'email':email,'firstName':'J02','lastName':str(index),'credentials':[{'type':'password','value':password,'temporary':False}]}
        r=client.post(base+'/admin/realms/k14/users',headers=headers,json=value)
        assert r.status_code==201,('create synthetic user',index,r.status_code)
        subject=r.headers['location'].rsplit('/',1)[1]
        user='user_'+secrets.token_hex(16)
        sql=f"INSERT INTO {schema}.hosted_identity_bindings(issuer,subject,user_id,display_name,email,email_verified,enabled,account_epoch,onboarding,last_workspace_id) VALUES ('{issuer}','{subject}','{user}','J02 actor {index}','{email}',true,true,0,'needs_workspace',NULL);"
        r=subprocess.run(['docker','exec','-i','aletheia-k14-stg-da2093353a73-pg','psql','-U','postgres','-d','aletheia','-X','-v','ON_ERROR_STOP=1'],input=sql.encode(),capture_output=True)
        assert r.returncode==0,('provision synthetic user',index)
        actors.append({'username':username,'user_id':user,'subject':subject,'email':email})
private.write_text(json.dumps({'run':run,'password':password,'actors':actors,'schema':schema,'issuer':issuer}),encoding='utf-8')
(folder/'fixture-public.json').write_text(json.dumps({'run':run,'actors':[{k:v for k,v in a.items() if k!='subject'} for a in actors],'schema':schema,'provisioning':'operator provisioned identities only; workspaces and memberships must be created through authenticated product endpoints'},indent=2),encoding='utf-8')
print('Provisioned',len(actors),'isolated identities; no workspace or membership seeded')
