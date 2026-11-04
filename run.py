"""Lancement en local : python run.py  ->  http://localhost:5000

Pour tester depuis la tablette ou le téléphone sur le même Wi-Fi : http://<ip-du-pc>:5000
"""
from home_management import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
