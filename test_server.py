import asyncio, json, os, shutil, sys, tempfile, time
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

def synth(d):
    now=int(time.time()*1000)
    mk=lambda i,t,**k:{**dict(id='t%d'%i,title=t,done=False,day='2026-11-14',sessions=0,updated=now),**k}
    json.dump({'v':1,'tasks':[mk(1,'Alpha',ord=10),mk(2,'Bravo',ord=20),mk(3,'Charlie',ord=30),mk(4,'Delta',ord=40),mk(5,'Echo',ord=50,starred=True),mk(6,'Foxtrot',later=True),mk(7,'Old done',done=True,doneDay='2026-11-01')],'deleted':[]},open(d+'/tasks.json','w'))
    json.dump({'v':1,'posts':[{'id':'p1','title':'Coffee morning recap','date':'2099-01-01','ready':True}],'deleted':[]},open(d+'/calendar.json','w'))

async def call(s,name,**a):
    r=await s.call_tool(name,a); return r.content[0].text

async def main(datadir):
    env={**os.environ,'TOMATO_DATA_DIR':datadir}
    params=StdioServerParameters(command=sys.executable,args=['server.py'],env=env)
    async with stdio_client(params) as (r,w):
        async with ClientSession(r,w) as s:
            await s.initialize()
            tools=await s.list_tools(); print('tools:',[t.name for t in tools.tools])
            print('\n--context\n'+await call(s,'get_context'))
            print('\n--add (top, starred=False)\n'+await call(s,'add_task',title='  Email   the plumber back ',position='top'))
            print('\n--delta to top\n'+await call(s,'update_task',task='delta',action='top'))
            print('\n--charlie later\n'+await call(s,'update_task',task='Charlie',action='later'))
            print('\n--bravo done\n'+await call(s,'update_task',task='bravo',action='done'))
            print('\n--ambiguous\n'+await call(s,'update_task',task='a',action='star'))
            print('\n--missing\n'+await call(s,'update_task',task='zzz',action='done'))
            print('\n--today\n'+await call(s,'list_tasks'))
            print('\n--later\n'+await call(s,'list_tasks',scope='later'))
            print('\n--done\n'+await call(s,'list_tasks',scope='done'))
d=tempfile.mkdtemp(); synth(d); asyncio.run(main(d))
