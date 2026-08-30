from flask import Flask

from database import preparar_base_para_startup
from concept_labels import alternative_display_label, human_concept_label

from routes.main import main_bp
from routes.sources import sources_bp
from routes.concepts import concepts_bp
from routes.occurrences import occurrences_bp
from routes.submissions import submissions_bp
from routes.alternatives import alternatives_bp


app = Flask(__name__)

app.jinja_env.filters["human_concept_label"] = human_concept_label
app.jinja_env.filters["alternative_display_label"] = alternative_display_label


# Registrar grupos de rutas
app.register_blueprint(main_bp)
app.register_blueprint(sources_bp)
app.register_blueprint(concepts_bp)
app.register_blueprint(occurrences_bp)
app.register_blueprint(submissions_bp)
app.register_blueprint(alternatives_bp)


# Preparar o validar la base activa antes de servir la aplicación
preparar_base_para_startup()


if __name__ == "__main__":
    app.run(debug=True)
