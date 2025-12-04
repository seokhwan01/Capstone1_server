from flask import Blueprint, render_template, request, redirect, session, url_for
from models.user import User
from extensions import db

bp = Blueprint("auth", __name__)
@bp.route("/login", methods=["GET", "POST"])
def login():
    db.session.expire_all()
    if request.method == "POST":
        uid = request.form["username"]
        pw = request.form["password"]
        user = User.query.filter_by(id=uid).first()
        if user and user.password == pw:
            session["user"] = uid
            return redirect(url_for("dashboard.dashboard"))
        return "로그인 실패"
    return render_template("login.html")

@bp.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("auth.login"))    
