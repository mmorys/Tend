from pathlib import Path
import re
from datetime import datetime
from fastapi import FastAPI,HTTPException
from fastapi.responses import FileResponse,Response
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel,Field
from typing import Literal
from . import __version__
from . import store as db
from . import ai
from . import commands
from . import storage
from .rendering import render
from .ai import reflect
from . import gitlab_secrets, gitlab_config, gitlab_sync

app=FastAPI(title="tether-chat")
app.add_middleware(TrustedHostMiddleware,allowed_hosts=["127.0.0.1","localhost","[::1]"])
db.init()
gitlab_sync.start()
class Ask(BaseModel):
    question:str=Field(min_length=1,max_length=1000)
class Thread(BaseModel):
    title:str=Field(min_length=1,max_length=300)
    summary:str=""
    categories:list[str]|None=Field(default=None,max_length=20)
class Change(BaseModel):
    title:str|None=None
    summary:str|None=None
    category:str|None=None
    categories:list[str]|None=Field(default=None,max_length=20)
    state:Literal["open","waiting","paused","settled"]|None=None
    revisit_at:datetime|None=None
    due_at:datetime|None=None
    recurrence:Literal["daily","weekly","monthly","quarterly","yearly"]|None=None
    next_occurrence:datetime|None=None
    due_action:Literal["activity_report"]|None=None
    action_config:dict|None=None
    action_prompt:str|None=None
    archived_at:str|None=None
class Entry(BaseModel):
    kind:Literal["thought","question","action","decision","update"]="thought"
    body:str=Field(min_length=1)
class ActionChange(BaseModel):
    done:bool|None=None
    due_at:datetime|None=None
    revisit_at:datetime|None=None
class Done(BaseModel):
    done:bool
class Relation(BaseModel):
    other_id:str
def found(x):
    if x is None:raise HTTPException(404,"Not found")
    return x
@app.get("/icon/{size}.png")
def icon_png(size:int):
    if size not in (16,32,48,64,128,192,256,512):
        raise HTTPException(404,"Icon size unavailable")
    from .windows_icon import raster
    return Response(raster(size),media_type="image/png",headers={"Cache-Control":"public, max-age=3600"})

@app.get("/favicon.svg")
def favicon():return FileResponse(Path(__file__).with_name("favicon.svg"),media_type="image/svg+xml")
@app.get("/theme.css")
def theme_css():return FileResponse(Path(__file__).with_name("theme.css"),media_type="text/css")
@app.get("/theme.js")
def theme_js():return FileResponse(Path(__file__).with_name("theme.js"),media_type="application/javascript")
@app.get("/api/health")
def health():return {"app":"tend","brand":"tether-chat","version":__version__,"status":"ok","streaming_chat":True,"board_categories":True,"tool_runner":"unbounded-v2"}
@app.get("/manifest.webmanifest")
def manifest():return FileResponse(Path(__file__).with_name("manifest.webmanifest"),media_type="application/manifest+json")
@app.get("/")
def home():return FileResponse(Path(__file__).with_name("index.html"),headers={"Cache-Control":"no-store"})
@app.get("/snapshots")
def snapshots_page():return FileResponse(Path(__file__).with_name("snapshot.html"))
@app.get("/api/snapshot")
def snapshot_api(start:str,end:str,category:str=""):
    from .snapshots import snapshot
    try:return snapshot(start,end,category)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None

@app.get("/api/snapshot.png")
def snapshot_png(start:str,end:str,category:str="",theme:str="sage",scale:int=2):
    from .snapshot_export import png
    try:
        return Response(png(start,end,category,theme,scale),media_type="image/png",
                        headers={"Content-Disposition":"attachment; filename=tend-snapshot.png",
                                 "Cache-Control":"no-store"})
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None

@app.get("/board")
def board_page():return FileResponse(Path(__file__).with_name("board.html"),headers={"Cache-Control":"no-store"})
@app.get("/api/snapshot.svg")
def snapshot_svg(start:str,end:str,category:str="",theme:str="sage"):
    from .snapshot_export import svg
    try:return Response(svg(start,end,category,theme),media_type="image/svg+xml",
                        headers={"Content-Disposition":"attachment; filename=tend-snapshot.svg","Cache-Control":"no-store"})
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
@app.get("/api/board")
def board():return db.board()
@app.get("/api/mentions")
def mentions():
    """Real, current link targets for interactive task/category/action references in chat."""
    tasks=[t for t in db.list_threads() if not t.get("archived_at")]
    actions=db.query("""SELECT e.id,e.thread_id,e.body,e.done,e.due_at,e.revisit_at
        FROM entries e JOIN threads t ON t.id=e.thread_id
        WHERE e.kind='action' AND t.archived_at IS NULL""")
    return {"categories":sorted({name for t in tasks for name in db.category_names(t)}),
            "tasks":[{"id":t["id"],"title":t["title"],"categories":t["categories"]} for t in tasks],
            "actions":actions}

@app.get("/api/threads")
def threads(q:str=""):return db.list_threads(q)
@app.get("/api/overview")
def overview():return db.overview()
@app.post("/api/threads",status_code=201)
def create(data:Thread):
    try:return db.create(data.title,data.summary,data.categories)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
@app.get("/api/threads/{id}")
def get(id:str):return found(db.get(id))
@app.patch("/api/threads/{id}")
def change(id:str,data:Change):
    fields=data.model_dump(exclude_unset=True)
    for field in ("revisit_at","due_at","next_occurrence"):
        if fields.get(field):fields[field]=fields[field].isoformat()
    try:return found(db.update(id,fields))
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
@app.get("/api/threads/{id}/due-action")
def thread_due_action(id:str):
    """Preview the configured task action; never invokes paid AI."""
    return db.report_for_task(id)

@app.get("/api/archived")
def archived(q:str=""):return db.archived_threads(q)

@app.post("/api/threads/{id}/archive")
def archive(id:str):return db.archive_thread(id,True)

@app.post("/api/threads/{id}/restore")
def restore(id:str):return db.archive_thread(id,False)

@app.delete("/api/threads/{id}")
def delete(id:str):return {"deleted":db.delete(id)}
@app.post("/api/threads/{id}/entries",status_code=201)
def add(id:str,data:Entry):return found(db.add(id,data.kind,data.body))
@app.patch("/api/actions/{id}")
def change_action(id:str,data:ActionChange):
    fields=data.model_dump(exclude_unset=True)
    for field in ("due_at","revisit_at"):
        if fields.get(field):fields[field]=fields[field].isoformat()
    try:return found(db.update_action(id,fields))
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
@app.patch("/api/entries/{id}")
def done(id:str,data:Done):return found(db.toggle(id,data.done))
@app.post("/api/threads/{id}/links")
def link(id:str,data:Relation):
    if not db.link(id,data.other_id):raise HTTPException(404,"Thread not found")
    return db.get(id)

@app.post("/api/reflect")
def reflection(data:Ask):
    try:return reflect(data.question)
    except ValueError as exc:raise HTTPException(502,str(exc)) from exc

class Message(BaseModel):
    text:str=Field(min_length=1,max_length=6000)
class Provider(BaseModel):
    base_url:str
    model:str
    api_key:str|None=None
    clear_key:bool=False

@app.get("/api/settings")
def settings():return ai.public_config()

@app.post("/api/settings")
def settings_save(data:Provider):
    try:return ai.save(data.base_url,data.model,data.api_key,data.clear_key)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None

class GitLabSettings(BaseModel):
    url:str|None=None
    token:str|None=None
    clear_token:bool=False

@app.get("/api/gitlab/settings")
def gitlab_settings():
    return {"url": gitlab_config.get_url(), "has_token": bool(gitlab_secrets.get())}

@app.post("/api/gitlab/settings")
def gitlab_settings_save(data:GitLabSettings):
    if data.url:
        gitlab_config.set_url(data.url)
    if data.clear_token:
        try:
            gitlab_secrets.delete_token()
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from None
    elif data.token:
        try:
            gitlab_secrets.set_token(data.token)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from None
    return {"url": gitlab_config.get_url(), "has_token": bool(gitlab_secrets.get())}

@app.get("/api/conversation")
def history():return db.conversation()

@app.post("/api/chat")
def chat(data:Message):
    message=data.text.strip()
    if not message:raise HTTPException(422,"Message required")
    try:
        from .snapshot_intent import respond as snapshot_respond
        result=snapshot_respond(message) or (commands.execute(message) if message.startswith("/") else ai.chat(message))
    except ValueError as exc:raise HTTPException(502,str(exc)) from exc
    db.remember("user",message)
    db.remember("assistant",result["answer"])
    return {"answer":result["answer"],"answer_html":render(result["answer"]),"actions":result.get("actions",[]),"history":db.conversation()}

@app.get("/api/backup")
def backup():
    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask
    from tempfile import NamedTemporaryFile
    import sqlite3
    import os
    file=NamedTemporaryFile(prefix="tether-chat-backup-",suffix=".sqlite3",delete=False)
    file.close()
    try:
        with sqlite3.connect(db.path()) as src, sqlite3.connect(file.name) as dest:src.backup(dest)
    except Exception:
        os.unlink(file.name)
        raise
    return FileResponse(file.name,filename="tether-chat-backup.sqlite3",media_type="application/octet-stream",
                        background=BackgroundTask(os.unlink,file.name))

@app.get("/api/conversation/rendered")
def rendered_history():
    return [{"id":row["id"],"role":row["role"],"body":row["body"],"html":render(row["body"]) if row["role"]=="assistant" else None,"created_at":row["created_at"]} for row in db.conversation()]

@app.get("/api/commands")
def command_reference():return commands.COMMANDS
@app.get("/api/brief")
def brief():return {"text":db.daily_brief(),"html":render(db.daily_brief())}

@app.get("/api/attention")
def attention(force:bool=False):
    from .attention import items
    return items(force)

class ActivityInput(BaseModel):
    body:str=Field(min_length=1,max_length=10000)
    kind:Literal["progress","feedback","decision","blocker","milestone","other"]="progress"
    occurred_at:datetime|None=None

class StorageLocation(BaseModel):
    path:str=Field(min_length=1,max_length=2048)
    mode:Literal["move","existing"]="move"

@app.post("/api/threads/{id}/activity",status_code=201)
def add_activity(id:str,data:ActivityInput):
    try:
        result=db.record_activity(id,data.body,data.kind,data.occurred_at.isoformat() if data.occurred_at else None)
        return found(result)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None

@app.get("/api/activity/report")
def report(period:Literal["week","month","quarter","year"]="week",anchor:datetime|None=None):
    try:return db.activity_report(period,anchor.isoformat() if anchor else None)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None

@app.get("/api/storage")
def storage_info():return storage.info()

@app.post("/api/storage")
def stage_storage(data:StorageLocation):
    try:return storage.stage(data.path,data.mode)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None

@app.post("/api/storage/choose-folder")
def select_storage_folder():
    try:return storage.choose_folder()
    except ValueError as exc:raise HTTPException(503,str(exc)) from None

class Occurrence(BaseModel):
    description:str="Completed recurring occurrence"
@app.post("/api/threads/{id}/complete-occurrence")
def complete_recurring(id:str,data:Occurrence):
    try:return db.complete_occurrence(id,data.description)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
@app.get("/api/recurring")
def recurring():return {"due":db.recurring_due(),"all":[t for t in db.list_threads() if t.get("recurrence")]}

@app.post("/api/chat/stream")
def chat_stream(data:Message):
    """NDJSON status stream; tool names only, never tool arguments or secrets."""
    import json,queue,threading
    from fastapi.responses import StreamingResponse
    message=data.text.strip()
    if not message:raise HTTPException(422,"Message required")
    def events():
        q=queue.Queue()
        def worker():
            try:
                from .snapshot_intent import respond as snapshot_respond
                result=snapshot_respond(message)
                if result is None:
                    if message.startswith("/"):
                        result=commands.execute(message)
                        if result is None:raise ValueError("Unknown command. Type /help.")
                    else:
                        result=ai.chat(message,progress=lambda value:q.put({"type":"status","message":value}))
                db.remember("user",message)
                db.remember("assistant",result["answer"])
                q.put({"type":"done","answer":result["answer"],"answer_html":render(result["answer"]),"snapshot":result.get("snapshot")})
            except Exception as exc:
                # Do not send raw provider exceptions or traceback to clients.
                q.put({"type":"error","message":str(exc) if isinstance(exc,ValueError) else "Assistant request failed. Check tether-chat server logs."})
            finally:q.put(None)
        threading.Thread(target=worker,daemon=True).start()
        while True:
            item=q.get()
            if item is None:break
            yield json.dumps(item,ensure_ascii=False)+"\n"
    return StreamingResponse(events(),media_type="application/x-ndjson",headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})

@app.post("/api/settings/test")
def test_ai_connection():
    try:
        response=ai.request([{"role":"user","content":"Respond with OK."}],tools=False,max_tokens=12)
        if not response.get("choices"):raise ValueError("Provider returned no response")
        return {"ok":True,"message":"Connection successful"}
    except ValueError as exc:raise HTTPException(502,str(exc)) from None


@app.get("/chat.js")
def chat_script():return FileResponse(Path(__file__).with_name("chat.js"),media_type="application/javascript")

@app.get("/action-notes.js")
def action_notes_script():return FileResponse(Path(__file__).with_name("action-notes.js"),media_type="application/javascript")

from .chat_api import router as chat_router
app.include_router(chat_router)
