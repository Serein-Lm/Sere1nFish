import asyncio, uuid
from datetime import datetime
from api.db.mongodb import init_mongo, get_db
from api.services.scholar_contact_pipeline import run_scholar_contact_collect

PID="6970c09e27b9715e54c7a83e"
TID="bulk_"+uuid.uuid4().hex[:8]

async def main():
    init_mongo(); db=get_db()
    await db["tasks"].insert_one({
        "task_id":TID,"project_id":PID,"task_type":"scholar_contact",
        "params":{"unit":"中山大学","unit_en":"Sun Yat-sen University","bulk":True,"max_articles":2000},
        "status":"running","progress":{},"created_at":datetime.now(),"updated_at":datetime.now(),
    })
    print("TASK", TID, "started", flush=True)
    out=await run_scholar_contact_collect(
        db, None, task_id=TID, project_id=PID,
        unit="中山大学", direction="", unit_en="Sun Yat-sen University",
        bulk=True, max_articles=2000, dry_run=False,
    )
    await db["tasks"].update_one({"task_id":TID},{"$set":{"status":out["status"],"updated_at":datetime.now(),"progress":{"articles":out["articles_total"],"contacts":out["contacts_total"]}}})
    print("DONE", {k:out[k] for k in ["matched_institution","articles_total","contacts_total","articles_inserted","contacts_inserted","corresponding_count","status","error"]}, flush=True)

asyncio.run(main())
