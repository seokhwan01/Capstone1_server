from flask import Blueprint, render_template, session, redirect, url_for

bp = Blueprint("dashboard", __name__)

@bp.route("/dashboard")
def dashboard():
    if "user" in session:
        return render_template("dashboard.html", username=session["user"])
    return redirect(url_for("auth.login"))
