from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from datetime import datetime, timedelta
import logging
import config
from daemons.auth import (
    login_required, verify_pin, is_locked_out,
    record_failed_attempt, clear_failed_attempts
)
from daemons.weekplan import (
    get_or_create_week, add_task, toggle_task, delete_task,
    set_task_recur, defer_task, duplicate_task, attach_file, reorder_section,
    set_task_note, rename_task,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(config.DATA_DIR / "app.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.permanent_session_lifetime = timedelta(hours=24)


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login_page():
    if not config.AUTH_ENABLED:
        session["authenticated"] = True
        return redirect(url_for("index"))

    if session.get("authenticated"):
        return redirect(url_for("index"))

    error = None
    locked_seconds = 0

    locked, remaining = is_locked_out()
    if locked:
        return render_template("login.html", error=None, locked_seconds=remaining)

    if request.method == "POST":
        pin = request.form.get("pin", "")
        if verify_pin(pin, config.PIN_HASH):
            clear_failed_attempts()
            session.permanent = True
            session["authenticated"] = True
            return redirect(url_for("index"))
        else:
            attempts_left, now_locked = record_failed_attempt()
            if now_locked:
                error = "Too many attempts. Locked for 5 minutes."
                locked_seconds = 300
            else:
                error = f"Wrong PIN. {attempts_left} attempts remaining."

    return render_template("login.html", error=error, locked_seconds=locked_seconds)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template("index.html")


# ── API ───────────────────────────────────────────────────────────────────────

@app.route("/api/week", methods=["GET"])
@login_required
def api_week_get():
    date_str = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
    return jsonify(get_or_create_week(date_str))


@app.route("/api/week/task", methods=["POST"])
@login_required
def api_task_add():
    data = request.get_json() or {}
    date_str = data.get("date", "")
    section = data.get("section", "")
    text = data.get("text", "")
    recur = data.get("recur") or None
    note = data.get("note") or None
    if not date_str or not section or not text:
        return jsonify({"error": "date, section, text required"}), 400
    result = add_task(date_str, section, text, recur, note)
    if "error" in result:
        return jsonify(result), 400
    logger.info(f"Task added: [{section}] {text}")
    return jsonify(result)


@app.route("/api/week/toggle", methods=["POST"])
@login_required
def api_task_toggle():
    data = request.get_json() or {}
    date_str = data.get("date", "")
    section = data.get("section", "")
    section_idx = data.get("section_idx")
    checked = data.get("checked")
    if not date_str or not section or section_idx is None or checked is None:
        return jsonify({"error": "date, section, section_idx, checked required"}), 400
    result = toggle_task(date_str, section, int(section_idx), bool(checked))
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/week/task", methods=["DELETE"])
@login_required
def api_task_delete():
    data = request.get_json() or {}
    date_str = data.get("date", "")
    section = data.get("section", "")
    section_idx = data.get("section_idx")
    if not date_str or not section or section_idx is None:
        return jsonify({"error": "date, section, section_idx required"}), 400
    result = delete_task(date_str, section, int(section_idx))
    if "error" in result:
        return jsonify(result), 400
    logger.info(f"Task deleted: [{section}] idx {section_idx}")
    return jsonify(result)


@app.route("/api/week/recur", methods=["POST"])
@login_required
def api_task_recur():
    data = request.get_json() or {}
    date_str = data.get("date", "")
    section = data.get("section", "")
    section_idx = data.get("section_idx")
    recur = data.get("recur", "")
    if not date_str or not section or section_idx is None:
        return jsonify({"error": "date, section, section_idx required"}), 400
    result = set_task_recur(date_str, section, int(section_idx), recur)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/week/defer", methods=["POST"])
@login_required
def api_task_defer():
    data = request.get_json() or {}
    date_str = data.get("date", "")
    section = data.get("section", "")
    section_idx = data.get("section_idx")
    defer_to = data.get("defer_to", "")
    if not date_str or not section or section_idx is None or not defer_to:
        return jsonify({"error": "date, section, section_idx, defer_to required"}), 400
    result = defer_task(date_str, section, int(section_idx), defer_to)
    if "error" in result:
        return jsonify(result), 400
    logger.info(f"Task deferred: [{section}] idx {section_idx} -> {defer_to}")
    return jsonify(result)


@app.route("/api/week/duplicate", methods=["POST"])
@login_required
def api_task_duplicate():
    data = request.get_json() or {}
    date_str = data.get("date", "")
    section = data.get("section", "")
    section_idx = data.get("section_idx")
    if not date_str or not section or section_idx is None:
        return jsonify({"error": "date, section, section_idx required"}), 400
    result = duplicate_task(date_str, section, int(section_idx))
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/week/attach", methods=["POST"])
@login_required
def api_task_attach():
    data = request.get_json() or {}
    date_str = data.get("date", "")
    section = data.get("section", "")
    section_idx = data.get("section_idx")
    filename = data.get("filename", "")
    if not date_str or not section or section_idx is None:
        return jsonify({"error": "date, section, section_idx required"}), 400
    result = attach_file(date_str, section, int(section_idx), filename)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/week/note", methods=["POST"])
@login_required
def api_task_note():
    data = request.get_json() or {}
    date_str = data.get("date", "")
    section = data.get("section", "")
    section_idx = data.get("section_idx")
    note = data.get("note", "")
    if not date_str or not section or section_idx is None:
        return jsonify({"error": "date, section, section_idx required"}), 400
    result = set_task_note(date_str, section, int(section_idx), note)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/week/rename", methods=["POST"])
@login_required
def api_task_rename():
    data = request.get_json() or {}
    date_str = data.get("date", "")
    section = data.get("section", "")
    section_idx = data.get("section_idx")
    new_text = data.get("text", "")
    if not date_str or not section or section_idx is None or not new_text:
        return jsonify({"error": "date, section, section_idx, text required"}), 400
    result = rename_task(date_str, section, int(section_idx), new_text)
    if "error" in result:
        return jsonify(result), 400
    logger.info(f"Task renamed: [{section}] idx {section_idx} -> {new_text}")
    return jsonify(result)


@app.route("/api/week/reorder", methods=["POST"])
@login_required
def api_task_reorder():
    data = request.get_json() or {}
    date_str = data.get("date", "")
    section = data.get("section", "")
    items = data.get("items", [])
    if not date_str or not section:
        return jsonify({"error": "date, section required"}), 400
    result = reorder_section(date_str, section, items)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5002)
