# Copiez ce fichier vers instance/config.py et adaptez les valeurs.
# Sur PythonAnywhere : /home/<utilisateur>/Home_Management/instance/config.py
#
# Générer une clé : python -c "import secrets; print(secrets.token_hex(32))"

SECRET_KEY = "remplacez-moi-par-une-longue-chaine-aleatoire"

# Mot de passe partagé du foyer, demandé une seule fois par appareil (session d'un an).
APP_PASSWORD = "choisissez-un-mot-de-passe"

# Cookies uniquement en HTTPS (PythonAnywhere sert le site en HTTPS).
SESSION_COOKIE_SECURE = True

TIMEZONE = "Europe/Paris"

# Facultatif : jeton du lien d'abonnement iCal. Le changer invalide les abonnements existants.
# ICS_TOKEN = "un-autre-jeton-secret"
