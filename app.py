from flask import Flask

from database import preparar_base_para_startup
from concept_labels import alternative_display_label, human_concept_label

from routes.main import main_bp
from routes.sources import sources_bp
from routes.concepts import concepts_bp
from routes.occurrences import occurrences_bp
from routes.submissions import submissions_bp
from routes.alternatives import alternatives_bp
from routes.collaborators import collaborators_bp
from routes.conflicts import conflicts_bp
from access_control import install_access_context
from source_period import format_source_period


app = Flask(__name__)

app.jinja_env.filters["human_concept_label"] = human_concept_label
app.jinja_env.filters["alternative_display_label"] = alternative_display_label
app.jinja_env.filters["source_period"] = format_source_period


# Registrar grupos de rutas
app.register_blueprint(main_bp)
app.register_blueprint(sources_bp)
app.register_blueprint(concepts_bp)
app.register_blueprint(occurrences_bp)
app.register_blueprint(submissions_bp)
app.register_blueprint(alternatives_bp)
app.register_blueprint(collaborators_bp)
app.register_blueprint(conflicts_bp)

install_access_context(app)


# Preparar o validar la base activa antes de servir la aplicación
preparar_base_para_startup()


if __name__ == "__main__":
    app.run(debug=True)
