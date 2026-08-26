from flask import Flask

from database import crear_base

from routes.main import main_bp
from routes.sources import sources_bp
from routes.concepts import concepts_bp
from routes.occurrences import occurrences_bp
from routes.submissions import submissions_bp


app = Flask(__name__)


# Registrar grupos de rutas
app.register_blueprint(main_bp)
app.register_blueprint(sources_bp)
app.register_blueprint(concepts_bp)
app.register_blueprint(occurrences_bp)
app.register_blueprint(submissions_bp)


# Crear tablas necesarias
crear_base()


if __name__ == "__main__":
    app.run(debug=True)
