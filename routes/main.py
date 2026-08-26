from flask import Blueprint, render_template


main_bp = Blueprint(
    "main",
    __name__
)


@main_bp.route("/trabajo")
def trabajo():

    return render_template(
        "trabajo.html"
    )