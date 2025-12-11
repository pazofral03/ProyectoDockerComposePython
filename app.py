import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io
import base64
import os
import json
import subprocess
from flask import Flask, render_template, request, redirect, url_for, flash
from pymongo import MongoClient
from bson.objectid import ObjectId
from bson import json_util # Necesario para exportar/importar datos de Mongo a JSON

app = Flask(__name__)
app.secret_key = 'super_secret_key' # Necesario para mostrar mensajes flash (notificaciones)

# --- CONFIGURACIÓN ---
MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')
client = MongoClient(MONGO_URI)
db = client['mi_negocio']
collection = db['ventas']
BACKUP_FILE = 'datos_backup.json'

# --- FUNCIONES AUXILIARES ---

def cargar_datos_desde_json():
    """Al iniciar, si la DB está vacía, carga desde el JSON."""
    if os.path.exists(BACKUP_FILE):
        with open(BACKUP_FILE, 'r') as file:
            data = json.load(file, object_hook=json_util.object_hook)
            if data:
                collection.insert_many(data)
                print(f"--- DATOS RESTAURADOS DESDE {BACKUP_FILE} ---")

def guardar_datos_en_json():
    """Vuelca todo Mongo a un archivo JSON."""
    datos = list(collection.find())
    with open(BACKUP_FILE, 'w') as file:
        json.dump(datos, file, default=json_util.default, indent=4)
    print("--- BACKUP JSON CREADO ---")

# En tu función ejecutar_git_push()
def ejecutar_git_push():
    """Ejecuta comandos de git para subir TODO el proyecto."""
    
    usuario = os.environ.get('GITHUB_USER')
    token = os.environ.get('GITHUB_TOKEN')
    repo_url = os.environ.get('GITHUB_REPO')

    if not usuario or not token or not repo_url:
        return False, "Error: Faltan variables de entorno GITHUB_..."

    auth_remote_url = f"https://{usuario}:{token}@{repo_url}"

    try:
        # 1. Configuración de seguridad para Docker (¡IMPORTANTE!)
        # Evita el error "detected dubious ownership in repository" al usar volúmenes
        subprocess.run(["git", "config", "--global", "--add", "safe.directory", "/app"], check=False)

        # 2. Identidad del Bot
        subprocess.run(["git", "config", "--global", "user.email", "bot@docker.local"], check=False)
        subprocess.run(["git", "config", "--global", "user.name", "Docker Backup Bot"], check=False)

        # 3. Inicialización (si hiciera falta)
        if not os.path.exists(".git"):
            subprocess.run(["git", "init"], check=True)
            subprocess.run(["git", "branch", "-M", "main"], check=True)
        
        # 4. Configurar el origen con el token
        subprocess.run(["git", "remote", "remove", "origin"], capture_output=True)
        subprocess.run(["git", "remote", "add", "origin", auth_remote_url], check=True)

        # --- EL CAMBIO CLAVE ESTÁ AQUÍ ---
        # Antes: ["git", "add", "datos_backup.json"]
        # Ahora: ["git", "add", "."] -> Añade TODO lo que no esté en .gitignore
        subprocess.run(["git", "add", "."], check=True)
        # ---------------------------------
        
        # 5. Commit y Push
        # Usamos check=False porque si no hay cambios nuevos, el commit falla y detiene el proceso
        subprocess.run(["git", "commit", "-m", "Auto-sync: Código y Datos actualizados desde Docker"], check=False)
        
        result = subprocess.run(["git", "push", "-u", "origin", "main"], capture_output=True, text=True)
        
        if result.returncode == 0:
            return True, "Sincronización COMPLETA (Código + DB) exitosa."
        else:
            return False, f"Error en Push: {result.stderr}"

    except Exception as e:
        return False, str(e)

# --- RUTAS ---

@app.route('/')
def dashboard():
    ventas = list(collection.find())
    if not ventas:
        return render_template('dashboard.html', plot_url=None)

    productos = [v['producto'] for v in ventas]
    cantidades = [int(v['cantidad']) for v in ventas]
    ingresos = [float(v['ingresos']) for v in ventas]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    ax1.bar(productos, cantidades, color='#4e73df')
    ax1.set_title('Cantidad Vendida')
    ax2.pie(ingresos, labels=productos, autopct='%1.1f%%', startangle=90)
    ax2.set_title('Distribución de Ingresos')

    img = io.BytesIO()
    plt.savefig(img, format='png', bbox_inches='tight')
    img.seek(0)
    plot_url = base64.b64encode(img.getvalue()).decode()
    plt.close()

    return render_template('dashboard.html', plot_url=plot_url)

@app.route('/gestion')
def gestion():
    ventas = list(collection.find())
    return render_template('gestion.html', ventas=ventas)

@app.route('/agregar', methods=['POST'])
def agregar():
    nuevo_dato = {
        "producto": request.form['producto'],
        "cantidad": int(request.form['cantidad']),
        "ingresos": float(request.form['ingresos'])
    }
    collection.insert_one(nuevo_dato)
    return redirect(url_for('gestion'))

@app.route('/eliminar/<id>')
def eliminar(id):
    collection.delete_one({'_id': ObjectId(id)})
    return redirect(url_for('gestion'))

# --- NUEVA RUTA: SINCRONIZAR ---
@app.route('/sincronizar')
def sincronizar():
    # 1. Guardar DB en JSON
    guardar_datos_en_json()
    
    # 2. Intentar Git Push
    exito, mensaje = ejecutar_git_push()
    
    if exito:
        flash(f'Éxito: {mensaje}', 'success')
    else:
        flash(f'Backup creado localmente, pero falló el Push: {mensaje}', 'warning')
        
    return redirect(url_for('gestion'))

if __name__ == '__main__':
    # Al arrancar, verificamos si hay datos. Si no, cargamos del backup.
    if collection.count_documents({}) == 0:
        cargar_datos_desde_json()
        
    app.run(host='0.0.0.0', port=5000, debug=True)