from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from datetime import datetime, timedelta
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
import logging
import requests
import config
from daemons.auth import (
    login_required, verify_pin, is_locked_out,
    record_failed_attempt, clear_failed_attempts
)
from daemons.weekplan import (
    init_db,
    get_or_create_week, add_task, toggle_task, delete_task, delete_task_all_future,
    set_task_recur, defer_task, duplicate_task, attach_file, reorder_section,
    set_task_note, rename_task, set_task_binding, toggle_step, set_step_count,
    set_task_color,
    list_birthdays, add_birthday, delete_birthday, bulk_set_birthday_reminder,
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
app.permanent_session_lifetime = timedelta(days=30)

init_db()


def _set_network_cookie(response):
    s = URLSafeTimedSerializer(config.SSO_SECRET)
    value = s.dumps({"net": "athena"}, salt="network-auth")
    response.set_cookie("network_auth", value, max_age=86400, httponly=True, samesite="Lax")
    return response


@app.before_request
def consume_sso_token():
    if request.endpoint == "login_page":
        return
    if session.get("authenticated"):
        if request.args.get("token"):
            return redirect(request.url.split("?")[0])
        return
    if not config.AUTH_ENABLED:
        return
    s = URLSafeTimedSerializer(config.SSO_SECRET)

    # Check URL token
    token = request.args.get("token")
    if token:
        try:
            data = s.loads(token, salt="sso-cross-app", max_age=300)
            if data.get("sso"):
                session.permanent = True
                session["authenticated"] = True
                resp = redirect(request.url.split("?")[0])
                _set_network_cookie(resp)
                return resp
        except (SignatureExpired, BadSignature):
            pass

    # Check shared network cookie
    net_cookie = request.cookies.get("network_auth")
    if net_cookie:
        try:
            data = s.loads(net_cookie, salt="network-auth", max_age=86400)
            if data.get("net"):
                session.permanent = True
                session["authenticated"] = True
                return
        except (SignatureExpired, BadSignature):
            pass


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
            resp = redirect(url_for("index"))
            _set_network_cookie(resp)
            return resp
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


@app.route("/api/auth-token")
@login_required
def api_auth_token():
    s = URLSafeTimedSerializer(config.SSO_SECRET)
    token = s.dumps({"sso": "athena"}, salt="sso-cross-app")
    return jsonify({"token": token})


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    build_time = datetime.now().strftime("%d %b %H:%M")
    return render_template("index.html", build_time=build_time)


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
    if bool(checked):
        today = datetime.now().strftime("%Y-%m-%d")
        headers = {'X-Internal-Token': config.INTERNAL_TOKEN}
        if result.get('nra_binding'):
            try:
                requests.post('http://localhost:5000/api/nra',
                              json={'items': [result['nra_binding']]},
                              headers=headers, timeout=2)
            except Exception as e:
                logger.warning(f"NRA binding call failed: {e}")
        if result.get('dwm_binding'):
            try:
                requests.post('http://localhost:5000/api/dwm/increment',
                              json={'slug': result['dwm_binding'], 'delta': 1, 'date': today},
                              headers=headers, timeout=2)
            except Exception as e:
                logger.warning(f"DWM binding call failed: {e}")
    return jsonify({"status": "saved"})


@app.route("/api/week/task", methods=["DELETE"])
@login_required
def api_task_delete():
    data = request.get_json() or {}
    date_str = data.get("date", "")
    section = data.get("section", "")
    section_idx = data.get("section_idx")
    if not date_str or not section or section_idx is None:
        return jsonify({"error": "date, section, section_idx required"}), 400
    scope = data.get("scope", "one")
    if scope == "all":
        result = delete_task_all_future(date_str, section, int(section_idx))
    else:
        result = delete_task(date_str, section, int(section_idx))
    if "error" in result:
        return jsonify(result), 400
    logger.info(f"Task deleted: [{section}] idx {section_idx} scope={scope}")
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
    items = data.get("ordered", [])
    if not date_str or not section:
        return jsonify({"error": "date, section required"}), 400
    result = reorder_section(date_str, section, items)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/week/color", methods=["POST"])
@login_required
def api_set_color():
    data = request.get_json() or {}
    date_str = data.get("date", "")
    section = data.get("section", "")
    section_idx = data.get("section_idx")
    color = data.get("color", "")
    if not date_str or not section or section_idx is None:
        return jsonify({"error": "date, section, section_idx required"}), 400
    result = set_task_color(date_str, section, int(section_idx), color)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/week/bind-nra", methods=["POST"])
@login_required
def api_task_bind_nra():
    data = request.get_json() or {}
    date_str = data.get("date", "")
    section = data.get("section", "")
    section_idx = data.get("section_idx")
    nra = data.get("nra", "")
    if not date_str or not section or section_idx is None:
        return jsonify({"error": "date, section, section_idx required"}), 400
    result = set_task_binding(date_str, section, int(section_idx), 'nra_binding', nra)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/week/bind-dwm", methods=["POST"])
@login_required
def api_task_bind_dwm():
    data = request.get_json() or {}
    date_str = data.get("date", "")
    section = data.get("section", "")
    section_idx = data.get("section_idx")
    dwm = data.get("dwm", "")
    if not date_str or not section or section_idx is None:
        return jsonify({"error": "date, section, section_idx required"}), 400
    result = set_task_binding(date_str, section, int(section_idx), 'dwm_binding', dwm)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/week/step-toggle", methods=["POST"])
@login_required
def api_step_toggle():
    data = request.get_json() or {}
    date_str = data.get("date", "")
    section = data.get("section", "")
    section_idx = data.get("section_idx")
    step_idx = data.get("step_idx")
    checked = data.get("checked")
    if not date_str or not section or section_idx is None or step_idx is None or checked is None:
        return jsonify({"error": "date, section, section_idx, step_idx, checked required"}), 400
    result = toggle_step(date_str, section, int(section_idx), int(step_idx), bool(checked))
    if "error" in result:
        return jsonify(result), 400
    if result.get("auto_complete"):
        toggle_result = toggle_task(date_str, section, int(section_idx), True)
        today = datetime.now().strftime("%Y-%m-%d")
        headers = {'X-Internal-Token': config.INTERNAL_TOKEN}
        if toggle_result.get('nra_binding'):
            try:
                requests.post('http://localhost:5000/api/nra',
                              json={'items': [toggle_result['nra_binding']]},
                              headers=headers, timeout=2)
            except Exception as e:
                logger.warning(f"NRA binding call failed: {e}")
        if toggle_result.get('dwm_binding'):
            try:
                requests.post('http://localhost:5000/api/dwm/increment',
                              json={'slug': toggle_result['dwm_binding'], 'delta': 1, 'date': today},
                              headers=headers, timeout=2)
            except Exception as e:
                logger.warning(f"DWM binding call failed: {e}")
    return jsonify({"status": "saved", "auto_complete": result.get("auto_complete", False)})


@app.route("/api/week/step-count", methods=["POST"])
@login_required
def api_step_count():
    data = request.get_json() or {}
    date_str = data.get("date", "")
    section = data.get("section", "")
    section_idx = data.get("section_idx")
    count = data.get("count", 0)
    if not date_str or not section or section_idx is None:
        return jsonify({"error": "date, section, section_idx required"}), 400
    result = set_step_count(date_str, section, int(section_idx), int(count))
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/proxy/nra", methods=["GET"])
@login_required
def api_proxy_nra():
    try:
        res = requests.get('http://localhost:5000/api/nra',
                           headers={'X-Internal-Token': config.INTERNAL_TOKEN},
                           timeout=3)
        return jsonify(res.json())
    except Exception as e:
        logger.warning(f"NRA proxy failed: {e}")
        return jsonify({"items": []}), 503


@app.route("/api/proxy/dwm", methods=["GET"])
@login_required
def api_proxy_dwm():
    try:
        res = requests.get('http://localhost:5000/api/dwm',
                           headers={'X-Internal-Token': config.INTERNAL_TOKEN},
                           timeout=3)
        return jsonify(res.json())
    except Exception as e:
        logger.warning(f"DWM proxy failed: {e}")
        return jsonify({"periods": {}}), 503


# ── Birthdays ─────────────────────────────────────────────────────────────────

@app.route("/api/birthdays", methods=["GET"])
@login_required
def api_birthdays_list():
    return jsonify(list_birthdays())


@app.route("/api/birthdays", methods=["POST"])
@login_required
def api_birthday_add():
    data = request.get_json() or {}
    name = data.get("name", "")
    month = data.get("month")
    day = data.get("day")
    year = data.get("year") or None
    if not name or month is None or day is None:
        return jsonify({"error": "name, month, day required"}), 400
    result = add_birthday(name, int(month), int(day),
                          int(year) if year else None)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/birthdays/<birthday_id>", methods=["DELETE"])
@login_required
def api_birthday_delete(birthday_id):
    return jsonify(delete_birthday(birthday_id))


@app.route("/api/birthdays/bulk-reminder", methods=["POST"])
@login_required
def api_birthday_bulk_reminder():
    data = request.get_json() or {}
    ids = data.get("ids", [])
    reminder_days = data.get("reminder_days")  # None clears reminder
    if not ids:
        return jsonify({"error": "ids required"}), 400
    result = bulk_set_birthday_reminder(ids, reminder_days)
    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5002)
