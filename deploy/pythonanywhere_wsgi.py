# Fichier WSGI pour PythonAnywhere.
# Copiez son contenu dans /var/www/<utilisateur>_pythonanywhere_com_wsgi.py
# (onglet « Web » > lien « WSGI configuration file »), puis remplacez <utilisateur>.
import sys

PROJECT = "/home/<utilisateur>/Home_Management"
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

# Les secrets sont lus dans /home/<utilisateur>/Home_Management/instance/config.py
# (voir deploy/config.example.py) : rien de sensible dans ce fichier.
from home_management import create_app  # noqa: E402

application = create_app()
