"""The ComfyUI-Mie web front end.

Four pages, one per thing the short-drama pipeline needs:

  /llm      the LLM platforms and models
  /assets   the character and scene assets
  /scripts  the script assets
  /video    the render

Everything the pages show comes from the same packages the ComfyUI nodes
use (``mie_craft`` for the assets and the script contract, ``mie_llm``
for the platforms), so the web app and the plugin cannot disagree about
what an asset is or what a valid script is.
"""

from __future__ import annotations

import json
import os
import pathlib

from flask import Flask, abort, jsonify, render_template, request, send_from_directory

from webapp import config
from webapp.services import (autodraft, comfy, history, library, llm,
                             riskfix, runname, runner, theme)


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64 MB uploads
    config.ensure_dirs()

    # The library resolves its root from this variable. It is read at call
    # time by mie_craft, but setting it here also keeps anything that
    # reads it at import time pointed at the right pack.
    os.environ.setdefault("MIE_PLUGIN_ROOT", str(config.PLUGIN_ROOT))

    # ------------------------------------------------------------------ #
    # pages
    # ------------------------------------------------------------------ #

    def page_context(**extra):
        theme_id = theme.load_saved_theme()
        context = {
            "theme_id": theme_id,
            "theme_css_content": theme.apply_theme(theme_id),
            "page_bg_color": theme.PAGE_BG_COLOR,
            "themes": theme.list_themes(),
        }
        context.update(extra)
        return context

    @app.route("/")
    def index():
        return render_template("video.html", **page_context(active="video"))

    @app.route("/llm")
    def llm_page():
        return render_template("llm.html", **page_context(active="llm"))

    @app.route("/assets")
    def assets_page():
        return render_template("assets.html", **page_context(active="assets"))

    @app.route("/scripts")
    def scripts_page():
        return render_template("scripts.html", **page_context(active="scripts"))

    @app.route("/video")
    def video_page():
        return render_template("video.html", **page_context(active="video"))

    @app.route("/history")
    def history_page():
        """The task history: what was made, what is still running, what
        the author wants to keep."""
        return render_template("history.html", **page_context(active="history"))

    # ------------------------------------------------------------------ #
    # theme and status
    # ------------------------------------------------------------------ #

    @app.route("/api/themes")
    def api_themes():
        return jsonify(theme.list_themes())

    @app.route("/api/theme", methods=["POST"])
    def api_set_theme():
        data = request.get_json(silent=True) or {}
        theme_id = theme.save_theme(str(data.get("theme") or ""))
        return jsonify({"theme": theme_id})

    @app.route("/api/comfy_status")
    def api_comfy_status():
        return jsonify(comfy.status())

    @app.route("/api/health")
    def api_health():
        """Who am I? The engine's node asks this to find out whether the
        web app is already up before it starts one."""
        return jsonify({
            "status": "ok",
            "app": "comfyui-mie-web",
            "plugin_root": str(config.PLUGIN_ROOT),
        })

    # ------------------------------------------------------------------ #
    # LLM platforms
    # ------------------------------------------------------------------ #

    @app.route("/api/llm/platforms")
    def api_llm_platforms():
        return jsonify({
            "platforms": llm.list_platforms(),
            "current": llm.current_platform(),
            "keys_file": str(config.LLM_KEYS_FILE),
        })

    @app.route("/api/llm/key", methods=["POST"])
    def api_llm_key():
        data = request.get_json(silent=True) or {}
        return jsonify(llm.set_key(str(data.get("key") or ""),
                                   str(data.get("token") or "")))

    @app.route("/api/llm/model", methods=["POST"])
    def api_llm_model():
        data = request.get_json(silent=True) or {}
        return jsonify(llm.set_model(str(data.get("key") or ""),
                                     str(data.get("model") or "")))

    @app.route("/api/llm/current", methods=["POST"])
    def api_llm_current():
        data = request.get_json(silent=True) or {}
        return jsonify(llm.set_current(str(data.get("key") or "")))

    @app.route("/api/llm/host", methods=["POST"])
    def api_llm_host():
        data = request.get_json(silent=True) or {}
        return jsonify(llm.set_host(str(data.get("key") or ""),
                                    str(data.get("host") or "")))

    @app.route("/api/llm/test", methods=["POST"])
    def api_llm_test():
        data = request.get_json(silent=True) or {}
        return jsonify(llm.test_connection(
            str(data.get("key") or ""), str(data.get("model") or ""),
            str(data.get("prompt") or "")))

    # ------------------------------------------------------------------ #
    # assets
    # ------------------------------------------------------------------ #

    @app.route("/api/assets")
    def api_assets():
        return jsonify(library.list_assets())

    @app.route("/api/assets/<kind>/<name>")
    def api_asset(kind, name):
        try:
            if kind == "character":
                return jsonify(library.get_character(name))
            if kind == "scene":
                return jsonify(library.get_scene(name))
        except (FileNotFoundError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 404
        abort(404)

    @app.route("/api/assets/<kind>", methods=["POST"])
    def api_save_asset(kind):
        # An upload is multipart, so the text fields arrive in
        # request.form - NOT in get_json(), which is empty for anything
        # that is not application/json. Both are accepted so the endpoint
        # works with a file and without one.
        data = request.get_json(silent=True) or {}
        form = request.form
        name = str(data.get("name") or form.get("name") or "")
        # A field the caller did not send is None, and None means "keep
        # what is on disk". An empty string is a real value: the caller
        # said so.
        description = _field(data, form, "description")
        voice = _field(data, form, "voice")
        image_raw = _uploaded("image")
        audio_raw = _uploaded("audio")
        try:
            if kind == "character":
                result = library.save_character(
                    name, description, voice,
                    image_raw=image_raw, audio_raw=audio_raw)
            elif kind == "scene":
                ambience = _field(data, form, "ambience")
                result = library.save_scene(
                    name, description, ambience=ambience,
                    image_raw=image_raw)
            else:
                abort(404)
        except (ValueError, FileNotFoundError) as exc:
            return jsonify({"error": str(exc)}), 400
        result.pop("png_bytes_list", None)
        result.pop("audio_bytes_list", None)
        return jsonify(result)

    @app.route("/api/assets/<kind>/<name>/delete", methods=["POST"])
    def api_delete_asset(kind, name):
        try:
            if kind == "character":
                return jsonify(library.delete_character(name))
            if kind == "scene":
                return jsonify(library.delete_scene(name))
        except (FileNotFoundError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 404
        abort(404)

    # ------------------------------------------------------------------ #
    # auto drafts (the web's version of the auto save / generator nodes)
    # ------------------------------------------------------------------ #

    @app.route("/api/assets/auto", methods=["POST"])
    def api_autodraft_asset():
        """Draft the EMPTY text fields of one character/scene asset.

        Nothing is written here: the draft lands in the edit form and the
        author saves it through the normal endpoint.
        """
        data = request.get_json(silent=True) or {}
        try:
            return jsonify(autodraft.asset_fields(
                str(data.get("kind") or ""),
                str(data.get("name") or ""),
                hint=str(data.get("hint") or ""),
                current=data.get("current") or {},
                image_data_url=str(data.get("image") or "") or None))
        except (ValueError, FileNotFoundError) as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001 - the provider's error is the answer
            return jsonify({"error": "%s: %s" % (type(exc).__name__, exc)}), 400

    # ------------------------------------------------------------------ #
    # scripts
    # ------------------------------------------------------------------ #

    @app.route("/api/scripts")
    def api_scripts():
        # ``items`` is additive metadata for the list view; ``names`` stays
        # the canonical field the video page and older clients read.
        return jsonify({
            "names": library.list_storyboards(),
            "items": library.list_storyboards_meta(),
        })

    @app.route("/api/scripts/<name>")
    def api_script(name):
        try:
            return jsonify(library.get_storyboard(name))
        except (FileNotFoundError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 404

    @app.route("/api/scripts", methods=["POST"])
    def api_save_script():
        data = request.get_json(silent=True) or {}
        payload = data.get("json")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError as exc:
                return jsonify({"error": "the JSON does not parse: %s" % exc}), 400
        try:
            return jsonify(library.save_storyboard(payload))
        except (ValueError, FileNotFoundError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/scripts/validate", methods=["POST"])
    def api_validate_script():
        data = request.get_json(silent=True) or {}
        payload = data.get("json")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError as exc:
                return jsonify({"ok": False, "errors": [str(exc)], "warnings": []})
        return jsonify(library.validate_storyboard(payload))

    @app.route("/api/scripts/<name>/delete", methods=["POST"])
    def api_delete_script(name):
        try:
            return jsonify(library.delete_storyboard(name))
        except (FileNotFoundError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 404

    @app.route("/api/scripts/<name>/confirm_execution", methods=["POST"])
    def api_confirm_execution(name):
        """Record that the English execution mirror matches the source.

        The mirror is written by hand, so nothing kept it in step with
        the Chinese source; this is the page's way of saying "I have
        looked at it, it is what I want".
        """
        try:
            script = library.get_storyboard(name)
        except (FileNotFoundError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 404
        return jsonify(library.confirm_execution(name, script["json"]))

    @app.route("/api/scripts/risk/plan", methods=["POST"])
    def api_risk_plan():
        """What to do about one render risk.

        Some risks are arithmetic and the app works them out itself -
        a shot that is too short for its lines is made longer, with the
        contract's own numbers. The rest need a decision, and the app
        hands the model the diagnosis together with the way to fix it.

        Nothing is changed here: this is the answer, not the action.
        """
        data = request.get_json(silent=True) or {}
        payload = data.get("json")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError as exc:
                return jsonify({"error": "剧本 JSON 解析不了：%s" % exc}), 400
        if not isinstance(payload, dict):
            return jsonify({"error": "没有剧本可改。"}), 400
        warnings = data.get("warnings")
        if warnings is None:
            warning = str(data.get("warning") or "")
            warnings = [warning] if warning else []
        if not warnings:
            return jsonify({"error": "没有说清是哪一条风险。"}), 400
        try:
            result = riskfix.plan_all(payload, warnings)
            if len(warnings) == 1:
                # 一条风险一个答案：页面上那一行问的就是这一条。
                result.update(riskfix.plan(payload, warnings[0]))
            return jsonify(result)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/scripts/risk/fix", methods=["POST"])
    def api_risk_fix():
        """Act on render risks - all of them, or the one that was named.

        The order is the whole point: the arithmetic first, so the
        model is handed a board that already fits, and the arithmetic
        once more afterwards, because the model rewrote the words and
        the budget has to be measured on what it wrote.

        Nothing is written to the library here. The result comes back
        as a draft and lands in the editor; the author looks at it and
        saves it like any other change.
        """
        data = request.get_json(silent=True) or {}
        payload = data.get("json")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError as exc:
                return jsonify({"error": "剧本 JSON 解析不了：%s" % exc}), 400
        if not isinstance(payload, dict):
            return jsonify({"error": "没有剧本可改。"}), 400
        warnings = data.get("warnings")
        if warnings is None:
            warning = str(data.get("warning") or "")
            warnings = [warning] if warning else []
        warnings = [str(w) for w in warnings if str(w).strip()]
        if not warnings:
            return jsonify({"error": "没有说清是哪一条风险。"}), 400

        def revise(request_text, board):
            """Hand the diagnosis to the model, on the board that fits."""
            drafted = autodraft.storyboard(
                "revise", request_text,
                base_asset=board,
                name=str(board.get("name") or "新剧本"),
                language=str(board.get("language") or "zh"),
                target_duration_seconds=float(
                    board.get("target_duration_seconds") or 20),
                audio_mode=str(board.get("audio_mode") or "generated"),
                critic="on")
            return drafted["json"]

        try:
            return jsonify(riskfix.fix_all(payload, warnings, revise=revise))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/scripts/<name>/execution/draft", methods=["POST"])
    def api_execution_draft(name):
        """Rewrite ONLY the English execution mirror, from the source.

        The author asks for this explicitly: the mirror is their text,
        and it is never replaced behind their back. The reply carries
        the new mirror and a field-by-field diff, so the page can show
        what would change before anything is replaced.

        Nothing is written here. The draft lands in the workbench
        editor, and it is saved like any other edit.
        """
        data = request.get_json(silent=True) or {}
        payload = data.get("json")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError as exc:
                return jsonify({"error": "剧本 JSON 解析不了：%s" % exc}), 400
        try:
            if not isinstance(payload, dict):
                script = library.get_storyboard(name)["json"]
            else:
                script = payload
        except (FileNotFoundError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 404
        try:
            return jsonify(autodraft.execution_mirror(script))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001 - the provider's error is the answer
            return jsonify({"error": "%s: %s" % (type(exc).__name__, exc)}), 400

    @app.route("/api/scripts/<name>/plan", methods=["POST"])
    def api_script_plan(name):
        data = request.get_json(silent=True) or {}
        role_assets = {str(k): str(v) for k, v in (data.get("role_assets") or {}).items()}
        try:
            return jsonify(runner.plan_preview(name, role_assets))
        except (FileNotFoundError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/scripts/auto", methods=["POST"])
    def api_autodraft_script():
        """Draft a storyboard from an idea (concept) or a change request
        (revise, with the open board as base_asset).

        Nothing is written here: the draft lands in the workbench editor
        and the author reviews it before the normal save.
        """
        data = request.get_json(silent=True) or {}
        try:
            return jsonify(autodraft.storyboard(
                str(data.get("mode") or "concept"),
                str(data.get("concept") or ""),
                base_asset=data.get("base_asset") or None,
                name=str(data.get("name") or "新剧本"),
                shot_count=int(data.get("shot_count") or 2),
                language=str(data.get("language") or "zh"),
                target_duration_seconds=float(data.get("target_duration_seconds") or 20),
                audio_mode=str(data.get("audio_mode") or "generated"),
                character_settings=str(data.get("character_settings") or ""),
                critic=str(data.get("critic") or "on")))
        except (ValueError, FileNotFoundError) as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001 - the provider's error is the answer
            return jsonify({"error": "%s: %s" % (type(exc).__name__, exc)}), 400

    # ------------------------------------------------------------------ #
    # render
    # ------------------------------------------------------------------ #

    @app.route("/api/render/submit", methods=["POST"])
    def api_render_submit():
        data = request.get_json(silent=True) or {}
        role_assets = {str(k): str(v) for k, v in (data.get("role_assets") or {}).items()}
        try:
            record = runner.submit(
                str(data.get("script") or ""), role_assets,
                run_name=str(data.get("run_name") or ""),
                width=int(data.get("width") or 960),
                height=int(data.get("height") or 544),
                steps=int(data.get("steps") or 20),
                duration=int(data.get("duration") or 18),
                continue_run=bool(data.get("continue_run")),
                continue_from=str(data.get("continue_from") or ""))
        except (FileNotFoundError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        except comfy.ComfyUnavailable as exc:
            return jsonify({"error": str(exc)}), 502
        return jsonify(record)

    @app.route("/api/render/jobs")
    def api_render_jobs():
        """The task history, one page at a time.

        The history grows with every render, so it is cut into pages
        here: the page asks for what it shows, and the files of the
        other pages are never looked at.
        """
        try:
            page = int(request.args.get("page") or 1)
        except (TypeError, ValueError):
            page = 1
        try:
            per_page = int(request.args.get("per_page") or 20)
        except (TypeError, ValueError):
            per_page = 20
        favorites = request.args.get("favorites") in ("1", "true", "yes")
        status = str(request.args.get("status") or "")
        return jsonify(runner.jobs(page=page, per_page=per_page,
                                   favorites_only=favorites, status=status))

    @app.route("/api/render/jobs/<job_id>/delete", methods=["POST"])
    def api_render_delete(job_id):
        """Take a run out of the history.

        The files stay on disk unless the author asks for them to go:
        a finished film is the one thing here that cannot be written
        again.
        """
        data = request.get_json(silent=True) or {}
        try:
            return jsonify(runner.delete_record(
                job_id, delete_files=bool(data.get("files"))))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404

    @app.route("/api/render/favorites", methods=["POST"])
    def api_render_favorite():
        """Mark or unmark a run as one to keep in sight."""
        data = request.get_json(silent=True) or {}
        run_name = str(data.get("run_name") or "")
        if not run_name:
            return jsonify({"error": "没说要收藏哪一条。"}), 400
        state = history.toggle_favorite(run_name)
        return jsonify({"run_name": run_name, "favorite": state})

    @app.route("/api/render/run_name", methods=["POST"])
    def api_render_run_name():
        """The run name a submission WOULD use, for the page to show.

        The engine takes the name verbatim, so the page shows the exact
        string instead of letting a Chinese script name become a
        surprise error at submit time. The version number comes with it:
        the author does not have to invent a name to keep the old
        result.
        """
        data = request.get_json(silent=True) or {}
        script = str(data.get("script") or "")
        return jsonify(runner.planned_run_name(
            script, str(data.get("run_name") or "")))

    @app.route("/api/render/<job_id>")
    def api_render_job(job_id):
        return jsonify(runner.job(job_id))

    # ------------------------------------------------------------------ #
    # files
    # ------------------------------------------------------------------ #

    @app.route("/media/<path:relpath>")
    def media(relpath):
        """A finished render's file."""
        root = comfy.media_root()
        target = (root / relpath).resolve()
        if not str(target).startswith(str(root.resolve())):
            abort(403)
        if not target.is_file():
            abort(404)
        return send_from_directory(root, relpath)

    @app.route("/library/<path:relpath>")
    def library_file(relpath):
        """An asset's picture, straight from the library."""
        root = (config.PLUGIN_ROOT / "asset_library").resolve()
        target = (root / relpath).resolve()
        if not str(target).startswith(str(root)):
            abort(403)
        if not target.is_file():
            abort(404)
        return send_from_directory(root, relpath)

    return app


def _field(data: dict, form, name: str):
    """One text field, from the JSON body or the form.

    None when the caller did not send it - which the service layer reads
    as "keep what is on disk".
    """
    if name in data:
        return data[name]
    if name in form:
        return form[name]
    return None


def _uploaded(field: str) -> bytes | None:
    """One uploaded file, as bytes; None when the field was not sent."""
    if field not in request.files:
        return None
    item = request.files[field]
    if not item or not item.filename:
        return None
    return item.read()


app = create_app()


if __name__ == "__main__":
    # Single process, no reloader: a second process would fight over the
    # render queue. The launcher-style start is in start_webui.bat.
    app.run(host=config.WEB_HOST, port=config.WEB_PORT,
            debug=config.WEB_DEBUG, use_reloader=False, threaded=True)
