# Copiez ce fichier vers instance/config.py et adaptez les valeurs.
# Sur PythonAnywhere : /home/<utilisateur>/Home_Management/instance/config.py
#
# Générer une clé : python -c "import secrets; print(secrets.token_hex(32))"

SECRET_KEY = "remplacez-moi-par-une-longue-chaine-aleatoire"

# Mot de passe INITIAL du premier foyer (identifiant « maison », administrateur).
# Il ne sert qu'à la création de la base : ensuite, chaque foyer change son mot de passe dans l'app
# (Réglages › Foyer). Le modifier ici après coup n'a plus d'effet.
APP_PASSWORD = "choisissez-un-mot-de-passe"

# Cookies uniquement en HTTPS (PythonAnywhere sert le site en HTTPS).
SESSION_COOKIE_SECURE = True

TIMEZONE = "Europe/Paris"

# Changer SECRET_KEY déconnecte tous les appareils de tous les foyers.
# Le lien d'abonnement iCal de chaque foyer se régénère dans l'app (Réglages › Abonnement calendrier).
