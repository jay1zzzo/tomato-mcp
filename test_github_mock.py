# Checks the GitHub code path against a fake GitHub API (no network, no token needed).
import base64, json, os, shutil, tempfile, time
os.environ.pop('TOMATO_DATA_DIR',None); os.environ['TOMATO_GITHUB_TOKEN']='fake'
import httpx, server
files={'tasks.json':json.dumps({'v':1,'tasks':[{'id':'a1','title':'Alpha','done':False,'day':'2026-11-14','sessions':0,'updated':1,'ord':10}],'deleted':[]})}
sha={'tasks.json':'s1'}; calls=[]; conflict_once=[True]
class R:
    def __init__(s,code,j=None): s.status_code=code; s._j=j
    def json(s): return s._j
    def raise_for_status(s):
        if s.status_code>=400: raise httpx.HTTPStatusError('x',request=None,response=None)
def name(url): return url.rsplit('/',1)[1]
def fget(url,headers,timeout):
    assert headers['Authorization']=='Bearer fake'; n=name(url); calls.append(('GET',n))
    raw=base64.encodebytes(files[n].encode()).decode()   # GitHub wraps base64 in newlines
    return R(200,{'content':raw,'sha':sha[n]})
def fput(url,headers,json,timeout):
    n=name(url); calls.append(('PUT',n))
    if conflict_once[0]:
        conflict_once[0]=False; sha[n]='s2'; return R(409)      # app synced meanwhile
    if json['sha']!=sha[n]: return R(409)
    files[n]=base64.b64decode(json['content']).decode(); sha[n]='s'+str(int(sha[n][1:])+1); return R(200,{})
httpx.get,httpx.put=fget,fput
print(server.add_task('From the MCP',position='top'))
print(calls)
d=json.loads(files['tasks.json']); print([ (t['title'],t.get('ord')) for t in d['tasks']])
