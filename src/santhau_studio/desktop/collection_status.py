"""Collection control states: IPC is authoritative; snapshot history is never live state."""


def apply_live_status(backend, payload, selected_job):
    worker = payload["worker"]
    runtime = worker.get("runtime") or {}
    current = runtime.get("current_job_id")
    pending = runtime.get("pending_actions") or {}
    payload["background"] = runtime.get("maintenance") or {"present": False}
    replies = {}
    known = {j.get("job_id") for group in ("active", "queued", "done") for j in payload[group]}
    # Durable maintenance has no queue document. Runtime identifies the current
    # target; its committed run and task metadata confirm controls and scope.
    if worker.get("pipe_ok"):
        ids = ([current] if current else []) + list(runtime.get("paused_jobs") or [])
        for job_id in dict.fromkeys(ids):
            if job_id in known:
                continue
            reply = backend.job_status(job_id)
            replies[job_id] = reply
            metadata = reply.get("request") or (runtime.get("current_job") if job_id == current else {}) or {}
            payload["active"].append({"job_id": job_id, **metadata})
            known.add(job_id)
    targets = list(payload["active"])
    last_job = runtime.get("last_job_id")
    targets.extend(j for j in payload["done"] if j.get("job_id") and j["job_id"] == last_job)
    if selected_job and selected_job not in targets:
        targets.append(selected_job)
    if worker.get("pipe_ok"):
        for entry in targets:
            job_id = entry.get("job_id")
            if not job_id:
                continue
            reply = replies.get(job_id) or backend.job_status(job_id)
            replies[job_id] = reply
            runs = reply.get("runs") or []
            if reply.get("ok") and not reply.get("error") and runs:
                entry.update(runs[-1])
                entry["live_confirmed"] = True
    for group in ("queued", "active", "done"):
        for entry in payload[group]:
            entry["queue_state"] = group
            lifecycle = entry.get("lifecycle")
            if entry.get("unreadable"):
                display = "UNREADABLE"
            elif group == "queued":
                display = "QUEUED"
            elif group == "done":
                display = entry.get("status") or "TERMINATED"
            elif not worker.get("running"):
                display = "WORKER_STOPPED"
            elif not worker.get("pipe_ok") or not entry.get("live_confirmed"):
                display = "STATUS_UNKNOWN"
            else:
                display = lifecycle or "STATUS_UNKNOWN"
            entry["display_state"] = display
            command = pending.get(entry.get("job_id"))
            entry["pending_command"] = command
            stopping = runtime.get("phase") == "STOPPING_WORKER"
            ready = bool(worker.get("pipe_ok") and entry.get("live_confirmed") and not command and not stopping)
            entry["can_pause"] = ready and group == "active" and lifecycle == "RUNNING"
            entry["can_resume"] = ready and group == "active" and lifecycle == "PAUSED"
            entry["can_stop"] = ready and group == "active" and lifecycle in ("RUNNING", "PAUSED")
    running = next((j for j in payload["active"] if current and j.get("job_id") == current), None)
    running = running or next((j for j in payload["active"] if j["display_state"] == "RUNNING"), None)
    if running:
        payload["active_status"] = replies.get(running["job_id"], {})
    elif selected_job and selected_job in payload["done"]:
        payload["last_status"] = replies.get(selected_job["job_id"], {})
    payload["counts"] = {
        "running": sum(j["display_state"] == "RUNNING" for j in payload["active"]),
        "paused": sum(j["display_state"] == "PAUSED" for j in payload["active"]),
        "unknown": sum(j["display_state"] in ("STATUS_UNKNOWN", "WORKER_STOPPED", "UNREADABLE") for j in payload["active"]),
        "queued": len(payload["queued"]), "done": len(payload["done"])}
