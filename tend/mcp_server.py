"""MCP stdio interface to the same local SQLite database as the browser app."""
import os
import sys
from mcp.server.fastmcp import FastMCP
from . import store as db
from . import gitlab_config, gitlab_secrets, gitlab_sync
mcp=FastMCP("tether-chat",host="127.0.0.1",port=int(os.environ.get("TEND_MCP_PORT","8766")))
@mcp.tool()
def get_snapshot(start:str,end:str,category:str="")->dict:
    """Get a read-only calendar for inclusive ISO dates. Each task appears once and carries all categories. A category filter matches any membership; category counts can overlap. Embed the view URL in chat on request; PNG/SVG download URLs need no AI generation."""
    from .snapshots import snapshot
    from urllib.parse import urlencode
    data=snapshot(start,end,category)
    query=urlencode({"start":start,"end":end,**({"category":category} if category else {})})
    data["view_url"]="/snapshots?"+query
    data["downloads"]={format:"/api/snapshot."+format+"?"+query for format in ("png","svg")}
    return data
@mcp.tool()
def task_link(thread_id:str)->dict:
    """Return a clickable local tether-chat task link, suitable for rich text or fallback navigation."""
    t=db.get(thread_id)
    return {"id":thread_id,"title":t["title"],"href":"/board#task/"+thread_id} if t else {"error":"Thread not found"}
@mcp.tool()
def whats_on_my_mind()->dict:
    """Return open, due, waiting, paused, and settled threads."""
    return db.overview()
@mcp.tool()
def daily_brief()->str:
    """Summarize due, active and waiting items for today."""
    return db.daily_brief()
@mcp.tool()
def due_reminders()->list[dict]:
    """List reminders that are currently due."""
    return db.overview()["due"]
@mcp.tool()
def categories()->list[dict]:
    """Show category groups. A task may belong to several groups; deduplicate by id for overall counts."""
    return db.board()
@mcp.tool()
def find_threads(query:str="")->list[dict]:
    """Search threads and their entries, or list all if no query."""
    return db.list_threads(query)
@mcp.tool()
def explore_thread(thread_id:str)->dict:
    """See full context, chronology, and relationships for a thread."""
    return db.get(thread_id) or {"error":"Thread not found"}
@mcp.tool()
def capture_thought(title:str,summary:str="",categories:list[str]|None=None)->dict:
    """Capture one task, optionally in several categories. Do not create a copy for each category."""
    return db.create(title,summary,categories)
@mcp.tool()
def update_action_item(action_id:str,due_at:str|None=None,revisit_at:str|None=None,done:bool|None=None)->dict:
    """Change one action's dates or completion without affecting sibling actions or its parent task. Only supply fields to change; empty string clears a date."""
    fields={}
    if due_at is not None:fields["due_at"]=due_at or None
    if revisit_at is not None:fields["revisit_at"]=revisit_at or None
    if done is not None:fields["done"]=done
    try:return db.update_action(action_id,fields) or {"error":"Action not found"}
    except ValueError as exc:return {"error":str(exc)}
@mcp.tool()
def add_action_item(task_id:str,text:str,due_at:str="",bring_to_attention_at:str="")->dict:
    """Create an action item under an existing task; skip matching duplicates. Action due dates and Bring to attention schedules apply only to the item. Times must include timezone."""
    previous=db.find_action(task_id,text)
    if previous:return {"action":previous,"already_exists":True}
    if due_at or bring_to_attention_at:
        fields={**({"due_at":due_at} if due_at else {}),**({"revisit_at":bring_to_attention_at} if bring_to_attention_at else {})}
        # Validate dates before creating anything, so invalid input never leaves orphan items.
        from datetime import datetime
        for stamp in fields.values():
            dt=datetime.fromisoformat(stamp.replace("Z","+00:00"))
            if dt.tzinfo is None:return {"error":"Action dates must have timezones"}
    entry=db.add(task_id,"action",text)
    if not entry:return {"error":"Task not found"}
    if due_at or bring_to_attention_at:entry=db.update_action(entry["id"],fields)
    return {"action":entry,"already_exists":False}
@mcp.tool()
def get_action_notes(action_id:str)->list[dict]|dict:
    """Read the notes attached only to the chosen action item."""
    notes=db.action_notes(action_id)
    return notes if notes is not None else {"error":"Action not found"}
@mcp.tool()
def add_action_note(action_id:str,text:str)->dict:
    """Save feedback or context under one action item; never attaches it to siblings or the task."""
    try:return db.add_action_note(action_id,text) or {"error":"Action not found"}
    except ValueError as exc:return {"error":str(exc)}
@mcp.tool()
def edit_action_note(action_id:str,note_id:str,text:str)->dict:
    """Edit an existing note belonging to the specified action."""
    try:return db.update_action_note(action_id,note_id,text) or {"error":"Note not found"}
    except ValueError as exc:return {"error":str(exc)}
@mcp.tool()
def delete_action_note(action_id:str,note_id:str)->dict:
    """Delete one action note only when requested; no other items are changed."""
    return {"deleted":db.delete_action_note(action_id,note_id)}
@mcp.tool()
def add_to_thread(thread_id:str,text:str,kind:str="thought")->dict:
    """Add an observation, question, action, decision, or update."""
    return db.add(thread_id,kind,text) or {"error":"Thread not found"}
@mcp.tool()
def revise_thread(thread_id:str,state:str="",summary:str="",revisit_at:str="",due_at:str="")->dict:
    """Update status, summary, or ISO timestamp for next revisit."""
    fields={}
    if state:fields["state"]=state
    if summary:fields["summary"]=summary
    if revisit_at:fields["revisit_at"]=revisit_at
    if due_at:fields["due_at"]=due_at
    return db.update(thread_id,fields) or {"error":"Thread not found"}
@mcp.tool()
def relate_threads(first_id:str,second_id:str)->dict:
    """Connect two related threads."""
    return {"linked":db.link(first_id,second_id)}
@mcp.tool()
def change_category(thread_id:str,category:str)->dict:
    """Replace all memberships with one category. For additional memberships use set_task_categories(mode='add')."""
    return db.update(thread_id,{"category":category}) or {"error":"Thread not found"}
@mcp.tool()
def set_task_categories(thread_id:str,categories:list[str],mode:str="replace")->dict:
    """Set several categories on one task. mode='add' preserves existing categories, 'remove' removes only named memberships, and 'replace' sets the whole list. An empty replacement resets to Unsorted. Returned categories lists every membership."""
    return db.set_categories(thread_id,categories,mode) or {"error":"Thread not found"}
@mcp.tool()
def log_activity(thread_id:str,text:str,kind:str="progress",occurred_at:str="")->dict:
    """Log work or feedback with optional ISO 8601 occurrence timestamp."""
    return db.record_activity(thread_id,text,kind,occurred_at or None) or {"error":"Thread not found"}
@mcp.tool()
def summarize_activity(period:str="week",anchor:str="")->dict:
    """Return structured activity for a week, month, quarter or year."""
    return db.activity_report(period,anchor or None)
@mcp.tool()
def recurring_items()->dict:
    """See all recurring threads and their next occurrences."""
    return {"due":db.recurring_due(),"all":[t for t in db.list_threads() if t.get("recurrence")]}
@mcp.tool()
def set_recurrence(thread_id:str,frequency:str,next_occurrence:str)->dict:
    """Configure a daily, weekly, monthly, quarterly or yearly recurrence."""
    return db.update(thread_id,{"recurrence":frequency,"next_occurrence":next_occurrence}) or {"error":"Thread not found"}
@mcp.tool()
def complete_recurring_occurrence(thread_id:str,description:str="Completed recurring occurrence")->dict:
    """Log completing a cycle and schedule the next recurrence."""
    return db.complete_occurrence(thread_id,description)
@mcp.tool()
def set_task_due_action(thread_id:str,action:str="activity_report",period:str="week",category:str="")->dict:
    """Attach an activity report to a task's reminder (or action='none' to clear)."""
    return db.configure_due_action(thread_id,action,period,category)
@mcp.tool()
def run_task_due_action(thread_id:str,anchor:str="")->dict:
    """Preview a task's linked reporting action, without any AI or modifying its recurrence."""
    return db.report_for_task(thread_id,anchor or None)
@mcp.tool()
def archive_task(thread_id:str)->dict:
    """Archive a task without deleting history."""
    return db.archive_thread(thread_id,True)
@mcp.tool()
def restore_task(thread_id:str)->dict:
    """Restore an archived task."""
    return db.archive_thread(thread_id,False)
@mcp.tool()
def list_archived_tasks(query:str="")->list[dict]:
    """Search archived task records."""
    return db.archived_threads(query)

@mcp.tool()
def gitlab_list_assigned_issues(state:str="opened")->list[dict]:
    """List GitLab issues assigned to the authenticated user. Requires GitLab connection configured in Settings. state can be opened, closed, all."""
    token = gitlab_secrets.get()
    if not token:
        return {"error":"GitLab token not configured"}
    url_base = gitlab_config.get_url()
    try:
        user = gitlab_sync._fetch_user(token, url_base)
        user_id = user.get("id")
    except Exception as e:
        return {"error": f"GitLab user fetch failed: {e}"}
    try:
        issues = gitlab_sync._paginate("/issues", token, url_base, {"assignee_id": user_id, "state": state, "per_page": 100})
    except Exception as e:
        return {"error": f"GitLab issues fetch failed: {e}"}
    # Normalize
    out = []
    for i in issues:
        out.append({
            "iid": i.get("iid"),
            "title": i.get("title"),
            "project_path": i.get("project_path_with_namespace"),
            "state": i.get("state"),
            "web_url": i.get("web_url"),
            "created_at": i.get("created_at"),
            "updated_at": i.get("updated_at")
        })
    return out

@mcp.tool()
def gitlab_list_assigned_mrs(state:str="opened")->list[dict]:
    """List GitLab merge requests where the authenticated user is a reviewer. Requires GitLab connection configured."""
    token = gitlab_secrets.get()
    if not token:
        return {"error":"GitLab token not configured"}
    url_base = gitlab_config.get_url()
    try:
        user = gitlab_sync._fetch_user(token, url_base)
        user_id = user.get("id")
    except Exception as e:
        return {"error": f"GitLab user fetch failed: {e}"}
    # Need projects where user is member
    try:
        projects = gitlab_sync._fetch_projects(token, url_base, user_id)
    except Exception as e:
        return {"error": f"Projects fetch failed: {e}"}
    mrs = []
    for p in projects:
        try:
            proj_mrs = gitlab_sync._fetch_mrs_for_project(token, url_base, p.get("id"), user_id)
        except Exception:
            continue
        for mr in proj_mrs:
            mrs.append({
                "iid": mr.get("iid"),
                "title": mr.get("title"),
                "project_path": mr.get("project_path"),
                "state": mr.get("state"),
                "web_url": mr.get("web_url"),
                "created_at": mr.get("created_at"),
                "updated_at": mr.get("updated_at")
            })
    return mrs

@mcp.tool()
def gitlab_create_task_from_issue(project_path:str,iid:int)->dict:
    """Create a tether-chat task from a GitLab issue. Requires GitLab connection configured."""
    token = gitlab_secrets.get()
    if not token:
        return {"error":"GitLab token not configured"}
    url_base = gitlab_config.get_url()
    try:
        # Fetch issue via API
        issue = gitlab_sync._request(f"/projects/{project_path.replace('/', '%2F')}/issues/{iid}", token, url_base)
    except Exception as e:
        return {"error": f"GitLab issue fetch failed: {e}"}
    title = f"GitLab Issue !{issue.get('iid')} in {issue.get('project_path_with_namespace')}"
    summary = issue.get("description") or ""
    categories = ["GitLab"]
    thread = db.create(title, summary, categories)
    # Store action config
    # Note: db.create returns dict with id
    # Add note with link
    if thread and thread.get("id"):
        db.add(thread["id"], "update", f"Linked to {issue.get('web_url')}")
    return {"task_id": thread.get("id") if thread else None, "title": title, "web_url": issue.get("web_url")}

if __name__=="__main__":
    db.init()
    mcp.run(transport="streamable-http" if "--http" in sys.argv else "stdio")
